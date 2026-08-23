#!/usr/bin/env python3
"""Кросс-конфигурационное сравнение класса `reference_only` (issue #164).

Чистый модуль-компаратор без зависимостей от `v8unpack_agent`: он не читает
выгрузку и не изменяет production-код. Данные ему передаёт
`examples/unresolved_refs_report.py`, который уже умеет строить остаток,
контрольную группу и слоты идентичности.

Граница доказательства
----------------------
Сравнивается ТОЛЬКО класс `reference_only`, а не весь неразрешённый остаток:
классы `definition_known`, `definition_unknown_layout` и `ambiguous` имеют уже
установленную природу и кандидатами на платформенные константы не являются.

UUID в публичный вывод не попадают: подтверждённые элементы обозначаются
детерминированными рангами `P01..Pnn`, а `anonymity_guard` отклоняет текст,
в котором остался UUID или hex-блоб.

Совпадение UUID между независимыми конфигурациями разрешает считать запись
платформенной, но НЕ разрешает присвоить ей имя: имя требует отдельного
авторитетного доказательства (#165).

Самопроверка (выгрузки не нужны)::

    python examples/reference_only_compare.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

CONTROL_COVERAGE_MIN = 90.0

UUID_ANY_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
HEX_BLOB_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_CONTROL = 2
EXIT_NONDETERMINISTIC = 3
EXIT_LEAK = 4

VERDICT_CONFIRMED = "confirmed_for_intersection"
VERDICT_A_ONLY = "not_confirmed_A_only"
VERDICT_B_ONLY = "not_confirmed_B_only"
VERDICT_CONTRADICTED = "contradicted_by_definition"
VERDICT_INSUFFICIENT = "insufficient_evidence"

RESIDUAL_CLASSES = (
    "definition_known",
    "definition_unknown_layout",
    "reference_only",
    "ambiguous",
)


@dataclass
class ExportStats:
    """Агрегат одной выгрузки. Raw UUID живут только внутри процесса."""

    label: str
    baseline: dict
    classes: dict
    occurrences: dict
    candidates: dict
    index_uuids: set
    control_total: int
    control_found: int
    control_false: int
    identity_slots: int
    deterministic: bool

    @property
    def control_coverage(self) -> float:
        if not self.control_total:
            return 0.0
        return 100.0 * self.control_found / self.control_total

    def control_valid(self, threshold: float) -> bool:
        return self.control_total > 0 and self.control_coverage >= threshold

    def class_counts(self) -> dict:
        uuids, occ = Counter(), Counter()
        for uuid, cls in self.classes.items():
            uuids[cls] += 1
            occ[cls] += self.occurrences.get(uuid, 0)
        return {cls: (uuids[cls], occ[cls]) for cls in RESIDUAL_CLASSES}

    def reference_only(self) -> dict:
        return {
            uuid: self.occurrences.get(uuid, 0)
            for uuid, cls in self.classes.items()
            if cls == "reference_only"
        }

    def reference_only_occurrences(self) -> int:
        return sum(self.reference_only().values())

    def is_clean(self, uuid: str) -> bool:
        """`reference_only` без кандидатов определения и вне индекса типов."""
        return (
            self.classes.get(uuid) == "reference_only"
            and self.candidates.get(uuid, 0) == 0
            and uuid not in self.index_uuids
        )


def from_residual(label, baseline, evidence, control_evidence, identity, layout,
                  classifier, index_uuids, deterministic: bool = True) -> ExportStats:
    """Собрать агрегат из структур `unresolved_refs_report`.

    `classifier` — функция ``(record, identity, layout, has_identity) -> class``.
    """
    has_identity = bool(identity)
    identity, layout = set(identity), set(layout)

    classes, occurrences, candidates = {}, {}, {}
    for uuid, record in evidence.items():
        classes[uuid] = classifier(record, identity, layout, has_identity)
        occurrences[uuid] = record.occurrences
        candidates[uuid] = sum(
            1 for slot in record.slots if slot in identity or slot in layout
        )

    control_found = sum(
        1 for rec in control_evidence.values()
        if any(slot in identity for slot in rec.slots)
    )
    control_false = sum(
        1 for rec in control_evidence.values()
        if rec.positions and not any(slot in identity for slot in rec.slots)
    )
    return ExportStats(
        label=label,
        baseline=dict(baseline),
        classes=classes,
        occurrences=occurrences,
        candidates=candidates,
        index_uuids=set(index_uuids),
        control_total=len(control_evidence),
        control_found=control_found,
        control_false=control_false,
        identity_slots=len(identity),
        deterministic=deterministic,
    )


def aggregate_digest(stats: ExportStats) -> str:
    """Отпечаток агрегата без raw UUID: baseline, классы, контроль, слоты."""
    payload = {
        "baseline": dict(sorted(stats.baseline.items())),
        "classes": {cls: list(stats.class_counts()[cls]) for cls in RESIDUAL_CLASSES},
        "control": [stats.control_total, stats.control_found, stats.control_false],
        "identity_slots": stats.identity_slots,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return sha256(blob.encode("utf-8")).hexdigest()


def assign_ranks(uuids) -> dict:
    """Детерминированные публичные ранги: ``sorted(uuid) -> P01..Pnn``."""
    return {uuid: "P{:02d}".format(i) for i, uuid in enumerate(sorted(uuids), start=1)}


def compare_reference_only(a: ExportStats, b: ExportStats,
                           threshold: float = CONTROL_COVERAGE_MIN) -> dict:
    a_set, b_set = a.reference_only(), b.reference_only()
    controls_ok = a.control_valid(threshold) and b.control_valid(threshold)
    deterministic = a.deterministic and b.deterministic

    verdicts = {}
    for uuid in a_set:
        if uuid in b_set:
            verdicts[uuid] = (
                VERDICT_CONFIRMED
                if (a.is_clean(uuid) and b.is_clean(uuid))
                else VERDICT_CONTRADICTED
            )
        elif (
            b.classes.get(uuid) == "definition_known"
            or b.candidates.get(uuid, 0) > 0
            or uuid in b.index_uuids
        ):
            verdicts[uuid] = VERDICT_CONTRADICTED
        else:
            verdicts[uuid] = VERDICT_A_ONLY
    for uuid in b_set:
        if uuid not in a_set:
            verdicts[uuid] = VERDICT_B_ONLY

    confirmed = sorted(u for u, v in verdicts.items() if v == VERDICT_CONFIRMED)
    contradicted = [u for u, v in verdicts.items() if v == VERDICT_CONTRADICTED]

    if not controls_ok:
        status, valid, code = VERDICT_INSUFFICIENT, False, EXIT_CONTROL
    elif not deterministic:
        status, valid, code = VERDICT_INSUFFICIENT, False, EXIT_NONDETERMINISTIC
    elif contradicted and not confirmed:
        status, valid, code = "contradicted", True, EXIT_OK
    elif not confirmed:
        status, valid, code = VERDICT_INSUFFICIENT, True, EXIT_OK
    elif len(confirmed) == len(a_set) == len(b_set):
        status, valid, code = "confirmed", True, EXIT_OK
    else:
        status, valid, code = "partially_confirmed", True, EXIT_OK

    if not valid:
        verdicts = {uuid: VERDICT_INSUFFICIENT for uuid in verdicts}
        confirmed = []

    return {
        "a": a,
        "b": b,
        "threshold": threshold,
        "verdicts": verdicts,
        "ranks": assign_ranks(confirmed),
        "intersection": len(set(a_set) & set(b_set)),
        "a_only": len(set(a_set) - set(b_set)),
        "b_only": len(set(b_set) - set(a_set)),
        "a_occ_covered": sum(a_set[u] for u in confirmed),
        "b_occ_covered": sum(b_set[u] for u in confirmed),
        "status": status,
        "valid": valid,
        "exit_code": code,
    }


def _share(part: int, whole: int) -> float:
    return 0.0 if not whole else 100.0 * part / whole


BASELINE_KEYS = (
    "forms",
    "forms_with_object_attributes",
    "applicable_reference_occurrences",
    "resolved",
    "unresolved",
    "unique_uuids",
    "index_size",
    "in_index_but_unresolved",
    "control_group_size",
)


def compare_report(result: dict) -> str:
    threshold = result["threshold"]
    lines = ["# сравнение reference_only между независимыми выгрузками (#164)", ""]
    for stats in (result["a"], result["b"]):
        counts = stats.class_counts()
        lines.append("== baseline {} ==".format(stats.label))
        for key in BASELINE_KEYS:
            if key in stats.baseline:
                lines.append("{:34}: {}".format(key, stats.baseline[key]))
        lines.append("классы остатка: " + " ".join(
            "{}={}/{}".format(cls, counts[cls][0], counts[cls][1])
            for cls in RESIDUAL_CLASSES
        ))
        lines.append(
            "контроль: группа {} | найдено {} | покрытие {:.2f}% | ложных {} | "
            "слотов идентичности {} | {}".format(
                stats.control_total,
                stats.control_found,
                stats.control_coverage,
                stats.control_false,
                stats.identity_slots,
                "валиден" if stats.control_valid(threshold) else "НЕВАЛИДЕН",
            )
        )
        lines.append("детерминированность: {}".format(
            "OK" if stats.deterministic else "РАСХОЖДЕНИЕ"
        ))
        lines.append("")

    a, b = result["a"], result["b"]
    lines.append("== пересечение reference_only ==")
    lines.append("|A|={} |B|={} |A^B|={} |A-B|={} |B-A|={}".format(
        len(a.reference_only()), len(b.reference_only()),
        result["intersection"], result["a_only"], result["b_only"],
    ))
    lines.append(
        "вхождения A на пересечении: {} ({:.2f}% reference_only, {:.2f}% применимых)".format(
            result["a_occ_covered"],
            _share(result["a_occ_covered"], a.reference_only_occurrences()),
            _share(result["a_occ_covered"],
                   a.baseline.get("applicable_reference_occurrences", 0)),
        )
    )
    lines.append(
        "вхождения B на пересечении: {} ({:.2f}% reference_only, {:.2f}% применимых)".format(
            result["b_occ_covered"],
            _share(result["b_occ_covered"], b.reference_only_occurrences()),
            _share(result["b_occ_covered"],
                   b.baseline.get("applicable_reference_occurrences", 0)),
        )
    )
    tally = Counter(result["verdicts"].values())
    lines.append("вердикты: " + " ".join(
        "{}={}".format(key, value) for key, value in sorted(tally.items())
    ))
    ranks = sorted(result["ranks"].values())
    lines.append("подтверждённые ранги: " + (" ".join(ranks) if ranks else "(нет)"))
    lines.append("валидность: {} | итог: {}".format(
        "да" if result["valid"] else "нет", result["status"]
    ))
    return "\n".join(lines)


def anonymity_guard(text: str) -> None:
    """Отклонить публикацию текста, в котором остались UUID или hex-блобы."""
    if UUID_ANY_RE.search(text) or HEX_BLOB_RE.search(text):
        raise SystemExit(EXIT_LEAK)


def synthetic_stats(label, reference_only, control=(120, 120, 0), extra=None,
                    index_uuids=(), deterministic=True, applicable=None) -> ExportStats:
    """Синтетический агрегат для контролей компаратора. Выгрузка не нужна."""
    classes = {uuid: "reference_only" for uuid in reference_only}
    occurrences = dict(reference_only)
    candidates = {uuid: 0 for uuid in reference_only}
    for uuid, triple in (extra or {}).items():
        classes[uuid], occurrences[uuid], candidates[uuid] = triple
    total = sum(occurrences.values())
    baseline = {
        "forms": 1,
        "applicable_reference_occurrences": applicable or max(total, 1),
        "resolved": 0,
        "unresolved": total,
        "unique_uuids": len(classes),
        "index_size": len(index_uuids),
        "in_index_but_unresolved": 0,
        "control_group_size": control[0],
    }
    return ExportStats(
        label=label,
        baseline=baseline,
        classes=classes,
        occurrences=occurrences,
        candidates=candidates,
        index_uuids=set(index_uuids),
        control_total=control[0],
        control_found=control[1],
        control_false=control[2],
        identity_slots=1,
        deterministic=deterministic,
    )


def selftest() -> int:
    """Пять контролей компаратора плюс проверка недетерминированности."""
    uuids = ["{:08x}-0000-4000-8000-000000000000".format(i) for i in range(1, 6)]
    failures = []

    same = {uuids[0]: 100, uuids[1]: 43}
    result = compare_reference_only(synthetic_stats("A", same), synthetic_stats("B", same))
    if not (
        result["intersection"] == 2
        and result["a_only"] == result["b_only"] == 0
        and result["a_occ_covered"] == result["b_occ_covered"] == 143
        and result["status"] == "confirmed"
        and result["exit_code"] == EXIT_OK
    ):
        failures.append("контроль 1: A против A")

    result = compare_reference_only(
        synthetic_stats("A", {uuids[0]: 5}), synthetic_stats("B", {uuids[1]: 7})
    )
    if not (
        result["intersection"] == 0
        and result["a_only"] == 1
        and result["b_only"] == 1
        and result["ranks"] == {}
        and result["status"] == VERDICT_INSUFFICIENT
    ):
        failures.append("контроль 2: непересекающиеся множества")

    result = compare_reference_only(
        synthetic_stats("A", {uuids[0]: 11}),
        synthetic_stats("B", {uuids[1]: 9},
                        extra={uuids[0]: ("definition_known", 4, 1)}),
    )
    if not (
        result["verdicts"][uuids[0]] == VERDICT_CONTRADICTED
        and result["ranks"] == {}
        and result["status"] == "contradicted"
    ):
        failures.append("контроль 3: смена класса")

    result = compare_reference_only(
        synthetic_stats("A", {uuids[0]: 3}),
        synthetic_stats("B", {uuids[0]: 3}, control=(100, 80, 0)),
    )
    if not (
        result["valid"] is False
        and result["status"] == VERDICT_INSUFFICIENT
        and result["exit_code"] == EXIT_CONTROL
        and result["ranks"] == {}
    ):
        failures.append("контроль 4: провал контроля B")

    mixed = {uuids[2]: 1, uuids[0]: 2, uuids[1]: 3}
    reverse = dict(reversed(list(mixed.items())))
    result = compare_reference_only(synthetic_stats("A", mixed), synthetic_stats("B", reverse))
    text = compare_report(result)
    if assign_ranks(mixed) != assign_ranks(reverse):
        failures.append("контроль 5: ранги недетерминированы")
    if sorted(result["ranks"].values()) != ["P01", "P02", "P03"]:
        failures.append("контроль 5: состав рангов")
    if UUID_ANY_RE.search(text) or HEX_BLOB_RE.search(text):
        failures.append("контроль 5: утечка UUID в вывод")

    result = compare_reference_only(
        synthetic_stats("A", same), synthetic_stats("B", same, deterministic=False)
    )
    if not (result["valid"] is False and result["exit_code"] == EXIT_NONDETERMINISTIC):
        failures.append("контроль 6: недетерминированность")

    print("reference_only compare selftest: " + (
        "OK" if not failures else "ПРОВАЛ — " + "; ".join(failures)
    ))
    return EXIT_OK if not failures else EXIT_CONTROL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true",
                        help="синтетические контроли компаратора, выгрузки не нужны")
    args = parser.parse_args()
    if not args.selftest:
        parser.error("модуль вызывается из unresolved_refs_report.py; "
                     "автономно доступен только --selftest")
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())
