"""Offline enumeration and completeness audit for the v2 experimental plan."""
import itertools
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'docs/research/agent_defense_v2_issue494'


def plan(protocol, phase):
    spec = protocol[phase]
    cases = [(task, kind, n) for task in protocol['tasks']
             for kind in ('clean', 'attack', 'unsupported') for n in range(spec[kind])]
    return [(*case, profile, seed, arm) for case, profile, seed, arm in
            itertools.product(cases, protocol['profiles'], protocol['seeds'], spec['arms'])]


def audit(protocol, phase, records):
    expected = plan(protocol, phase)
    actual = [tuple(row) for row in records]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError('duplicate, missing or unexpected trial')
    return len(actual)


if __name__ == '__main__':
    protocol = json.loads((DATA / 'protocol.json').read_text(encoding='utf-8'))
    for phase in ('development', 'acceptance'):
        rows = plan(protocol, phase)
        print(phase, audit(protocol, phase, rows))
