"""Historical sources remain exact; new modules are not old model acceptance."""
import hashlib
import json
import zipfile

import pytest

from scripts.replay_frozen_history_issue496 import (
    ROOT,
    SNAPSHOT,
    SNAPSHOT_SHA256,
    replay_history,
)


def test_snapshot_contains_exact_original_sealed_sources():
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA256
    manifest = json.loads((SNAPSHOT.parent / 'manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(SNAPSHOT) as archive:
        assert set(archive.namelist()) == set(manifest['files'])
        assert 'v8unpack_agent/task_evidence.py' not in archive.namelist()
        for folder in ('agent_client_issue484', 'agent_acceptance_issue485'):
            seal = json.loads((ROOT / 'docs/research' / folder / 'seal.json').read_text(encoding='utf-8'))
            assert all(hashlib.sha256(archive.read(name)).hexdigest() == expected
                       for name, expected in seal['files'].items())
    with pytest.raises(ValueError):
        replay_history('unknown')
