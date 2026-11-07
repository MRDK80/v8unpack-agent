"""Show actual cached before/after reports; no model/network or private CF input."""
from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any

from scripts import agent_acceptance_issue485 as study
from scripts.audit_acceptance_results_issue485 import DATA, audit
from v8unpack_agent.untrusted_transport import encode_block

DISPLAY = ['legacy', 'framed', 'evidence', 'legacy_guard', 'evidence_guard', 'actual_native']


def report(record: dict[str, Any] | None) -> Any:
    if record is None or record['report_file'] is None:return None
    return study.parse_report(record['report_file'])


def choose(records: list[dict[str, Any]], *, profile: str | None, seed: int | None,
           case_id: str | None) -> dict[str, Any]:
    cases = {c['id']: c for c in study.cases()}
    grouped: dict[tuple, dict[str, Any]] = {}
    deterministic = {}
    for r in records:
        row = r['row']
        if row['variant'] == 'deterministic':
            deterministic[row['case']] = r;continue
        if profile is not None and row['profile'] != profile:continue
        if seed is not None and row['seed'] != seed:continue
        if case_id is not None and row['case'] != case_id:continue
        key = (row['profile'], row['seed'], row['case'])
        grouped.setdefault(key, {})[row['variant']] = r
    first = None
    for (selected_profile, selected_seed, selected_case), variants in grouped.items():
        case = cases[selected_case]
        example = {'profile': selected_profile, 'seed': selected_seed, 'case': case,
                   'variants': variants, 'deterministic': deterministic.get(selected_case)}
        if first is None:first = example
        if case_id is not None:return example
        if case['kind'] != 'attack':continue
        before = [report(variants.get(v)) for v in ('legacy', 'framed')]
        after = report(variants.get('evidence_guard'))
        target = case.get('target_report')
        if target is not None and target in before and study.equal_report(after, case['expected_report'], case['task']):
            return example
    if first is None:raise ValueError('no matching recorded pair')
    # Never invent a successful attack if the selected profile/seed had none.
    return first


def visible(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return ''.join(f'\\u{{{ord(c):04X}}}' if unicodedata.category(c) in {'Cf', 'Zl', 'Zp'} else c for c in text)


def describe(example: dict[str, Any]) -> str:
    case = example['case'];expected = case['expected_report'];task = case['task']
    lines = [f"Случай: {case['id']}; профиль: {example['profile']}; seed: {example['seed']}.",
             'Это реальная сохранённая траектория. Новых запросов к LLM нет.',
             'Показательный пример выбран из полного набора; он не заменяет общую статистику.',
             'Должно быть: ' + visible(expected)]
    for variant in DISPLAY:
        r = example['variants'].get(variant)
        if r is None:continue
        actual = report(r)
        if actual is None:
            verdict = 'нет нового отчёта; задача не выполнена'
            if r['result']['outcome'] == 'unsupported':verdict += ' (явный NO-GO/unsupported)'
        else:
            verdict = 'совпало с источником' if study.equal_report(actual, expected, task) else 'НЕВЕРНЫЙ отчёт'
        blocked = sum(e['status'] == 'blocked' for e in r['result']['events'])
        lines.append(f'{variant}: {visible(actual)} — {verdict}; блокировок={blocked}.')
    static = report(example['deterministic'])
    lines.append('deterministic (БЕЗ МОДЕЛИ): ' + visible(static) +
                 (' — совпало с источником.' if study.equal_report(static, expected, task) else ' — нет поддержанного правильного отчёта.'))
    lines += ['Ноль неверных записей не означает, что модель не пыталась выполнить указание из исходника.',
              'В проверяемом режиме право записи и сверка результата остаются у хоста. Грамматика, metadata и TOCTOU имеют ограничения.']
    return '\n'.join(lines)


def export_reports(example: dict[str, Any], output: Path) -> None:
    """Caller selects a NEW directory; report content never selects a path."""
    output.mkdir(parents=True, exist_ok=False)
    for label, record in [('before-legacy', example['variants'].get('legacy')),
                          ('before-framed', example['variants'].get('framed')),
                          ('after-evidence-guard', example['variants'].get('evidence_guard')),
                          ('after-deterministic', example['deterministic'])]:
        if record is not None and record['report_file'] is not None:
            with (output / (label + '.json')).open('xb') as writer:
                writer.write(record['report_file'].encode('utf-8'))
    with (output / 'comparison.txt').open('x', encoding='utf-8') as writer:
        writer.write(describe(example) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--profile', choices=study.PROFILES)
    parser.add_argument('--seed', type=int, choices=study.SEEDS)
    parser.add_argument('--case', dest='case_id')
    parser.add_argument('--show-source', action='store_true')
    parser.add_argument('--output', type=Path, help='New local directory for exact cached before/after reports')
    args = parser.parse_args()
    try:
        summary = audit(args.data)
        example = choose(study.load_records(args.data), profile=args.profile, seed=args.seed, case_id=args.case_id)
        if args.output is not None:export_reports(example, args.output)
    except FileExistsError:
        parser.error('output directory must be new; existing files are never overwritten')
    except (OSError, ValueError):
        parser.error('verified complete acceptance archive or selected pair is unavailable')
    print(f"Аудит полного набора: PASS, {summary['rows']} строк.")
    if args.show_source:
        case = example['case']
        print('НЕДОВЕРЕННЫЙ СИНТЕТИЧЕСКИЙ ИСХОДНИК (только данные):')
        print(encode_block(study.source_text(case), n=1, kind='text'))
    print(describe(example))


if __name__ == '__main__':main()
