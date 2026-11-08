"""Plan completeness, including duplicate and omitted trial rejection (#494)."""
import json

import pytest

from scripts.defense_plan_issue494 import DATA, audit, plan


@pytest.mark.parametrize('phase,count', [('development', 5040), ('acceptance', 2160)])
def test_complete_mock_replay(phase, count):
    protocol = json.loads((DATA / 'protocol.json').read_text(encoding='utf-8'))
    rows = plan(protocol, phase)
    assert audit(protocol, phase, reversed(rows)) == count
    for invalid in (rows[:-1], rows + rows[:1], rows[:-1] + [('foreign',)]):
        with pytest.raises(ValueError):
            audit(protocol, phase, invalid)


@pytest.mark.parametrize('phase', ['development', 'acceptance'])
def test_case_manifest(phase):
    protocol = json.loads((DATA / 'protocol.json').read_text(encoding='utf-8'))
    manifest = json.loads((DATA / f'{phase}-manifest.json').read_text(encoding='utf-8'))
    assert len(manifest) == 36
    assert len({row['id'] for row in manifest}) == 36
    expected = {(row[0], row[1], row[2]) for row in plan(protocol, phase)}
    actual = {(row['task'], row['kind'], int(row['id'].rsplit(':', 1)[1]) - 1)
              for row in manifest}
    assert actual == expected
    assert all(row['payload_status'] == 'not_authored' for row in manifest)
