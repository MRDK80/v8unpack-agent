"""Контроли компаратора класса reference_only (issue #164).

Выгрузка не требуется: все проверки идут на синтетических агрегатах и на
структурах, повторяющих модель `unresolved_refs_report.Evidence`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "reference_only_compare.py"
)


@pytest.fixture(scope="module")
def cmp_module():
    spec = importlib.util.spec_from_file_location("reference_only_compare", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Модуль обязан попасть в sys.modules до exec_module: при
    # `from __future__ import annotations` dataclasses резолвит строковые
    # аннотации через sys.modules[cls.__module__] и иначе падает с
    # AttributeError на py3.12.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    yield module
    sys.modules.pop(spec.name, None)


@dataclass
class FakeEvidence:
    occurrences: int = 0
    positions: int = 0
    slots: Counter = field(default_factory=Counter)
    slot_files: dict = field(default_factory=lambda: defaultdict(set))


def fake_classify(record, identity, layout, has_identity):
    if not has_identity:
        return "ambiguous"
    if any(slot in identity for slot in record.slots):
        return "definition_known"
    for slot in record.slots:
        if slot in layout and record.slots[slot] == 1 and len(record.slot_files.get(slot, ())) == 1:
            return "definition_unknown_layout"
    return "reference_only" if record.positions else "ambiguous"


def uuid_at(index: int) -> str:
    return "{:08x}-0000-4000-8000-000000000000".format(index)


def test_self_comparison_is_total(cmp_module):
    same = {uuid_at(1): 100, uuid_at(2): 43}
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", same), cmp_module.synthetic_stats("B", same)
    )
    assert result["intersection"] == 2
    assert result["a_only"] == result["b_only"] == 0
    assert result["a_occ_covered"] == result["b_occ_covered"] == 143
    assert result["status"] == "confirmed"
    assert result["exit_code"] == cmp_module.EXIT_OK


def test_disjoint_sets_are_insufficient(cmp_module):
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", {uuid_at(1): 5}),
        cmp_module.synthetic_stats("B", {uuid_at(2): 7}),
    )
    assert result["intersection"] == 0
    assert (result["a_only"], result["b_only"]) == (1, 1)
    assert result["ranks"] == {}
    assert result["status"] == cmp_module.VERDICT_INSUFFICIENT


def test_class_change_is_not_confirmed(cmp_module):
    uuid = uuid_at(1)
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", {uuid: 11}),
        cmp_module.synthetic_stats(
            "B", {uuid_at(2): 9}, extra={uuid: ("definition_known", 4, 1)}
        ),
    )
    assert result["verdicts"][uuid] == cmp_module.VERDICT_CONTRADICTED
    assert result["ranks"] == {}
    assert result["status"] == "contradicted"


def test_uuid_in_reference_types_is_not_confirmed(cmp_module):
    uuid = uuid_at(1)
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", {uuid: 8}),
        cmp_module.synthetic_stats("B", {uuid: 8}, index_uuids=(uuid,)),
    )
    assert result["verdicts"][uuid] == cmp_module.VERDICT_CONTRADICTED
    assert result["ranks"] == {}


def test_failed_control_invalidates_comparison(cmp_module):
    uuid = uuid_at(1)
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", {uuid: 3}),
        cmp_module.synthetic_stats("B", {uuid: 3}, control=(100, 80, 0)),
    )
    assert result["valid"] is False
    assert result["status"] == cmp_module.VERDICT_INSUFFICIENT
    assert result["exit_code"] == cmp_module.EXIT_CONTROL
    assert result["ranks"] == {}


def test_nondeterministic_run_invalidates_comparison(cmp_module):
    same = {uuid_at(1): 4}
    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", same),
        cmp_module.synthetic_stats("B", same, deterministic=False),
    )
    assert result["valid"] is False
    assert result["exit_code"] == cmp_module.EXIT_NONDETERMINISTIC


def test_ranks_are_deterministic_and_output_is_anonymous(cmp_module):
    mixed = {uuid_at(3): 1, uuid_at(1): 2, uuid_at(2): 3}
    reverse = dict(reversed(list(mixed.items())))
    assert cmp_module.assign_ranks(mixed) == cmp_module.assign_ranks(reverse)

    result = cmp_module.compare_reference_only(
        cmp_module.synthetic_stats("A", mixed), cmp_module.synthetic_stats("B", reverse)
    )
    assert sorted(result["ranks"].values()) == ["P01", "P02", "P03"]

    text = cmp_module.compare_report(result)
    assert not cmp_module.UUID_ANY_RE.search(text)
    assert not cmp_module.HEX_BLOB_RE.search(text)
    cmp_module.anonymity_guard(text)


def test_anonymity_guard_rejects_leaked_uuid(cmp_module):
    with pytest.raises(SystemExit) as excinfo:
        cmp_module.anonymity_guard("P01 " + uuid_at(7))
    assert excinfo.value.code == cmp_module.EXIT_LEAK


def test_occurrence_shares_use_reference_only_and_applicable(cmp_module):
    stats_a = cmp_module.synthetic_stats(
        "A", {uuid_at(i): 50 for i in range(1, 5)}, applicable=1000
    )
    stats_b = cmp_module.synthetic_stats(
        "B", {uuid_at(i): 20 for i in range(1, 3)}, applicable=400
    )
    result = cmp_module.compare_reference_only(stats_a, stats_b)
    assert result["intersection"] == 2
    assert result["a_occ_covered"] == 100
    assert result["b_occ_covered"] == 40
    assert result["status"] == "partially_confirmed"

    text = cmp_module.compare_report(result)
    assert "50.00% reference_only" in text
    assert "10.00% применимых" in text


def test_from_residual_matches_report_model(cmp_module):
    identity_slot = "object_metadata|bare|header/*/identity"
    control = {}
    for i in range(10):
        record = FakeEvidence(positions=1)
        if i < 9:
            record.slots[identity_slot] += 1
            record.slot_files[identity_slot].add(i)
        control["c{:07x}-0000-4000-8000-000000000000".format(i)] = record

    ref_only = FakeEvidence(occurrences=57, positions=3)
    ref_only.slots["form_file|Ref#|form/data"] += 3
    known = FakeEvidence(occurrences=6, positions=1)
    known.slots[identity_slot] += 1
    known.slot_files[identity_slot].add(99)
    evidence = {uuid_at(1): ref_only, uuid_at(2): known}

    baseline = {"applicable_reference_occurrences": 15723, "control_group_size": 10}
    stats = cmp_module.from_residual(
        "A", baseline, evidence, control, {identity_slot}, set(),
        fake_classify, {"index-uuid"}, True,
    )

    counts = stats.class_counts()
    assert counts["reference_only"] == (1, 57)
    assert counts["definition_known"] == (1, 6)
    assert stats.control_coverage == pytest.approx(90.0)
    assert stats.control_valid(90.0) is True
    assert stats.control_false == 1
    assert stats.is_clean(uuid_at(1)) is True
    assert stats.is_clean(uuid_at(2)) is False
    assert cmp_module.aggregate_digest(stats) == cmp_module.aggregate_digest(stats)


def test_selftest_entrypoint_returns_zero(cmp_module, capsys):
    assert cmp_module.selftest() == cmp_module.EXIT_OK
    assert "OK" in capsys.readouterr().out
