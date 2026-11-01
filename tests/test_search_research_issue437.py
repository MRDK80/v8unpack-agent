"""Сборщик замера #437: производная от #370 с явным SHA и связью layout."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_search_issue437.py"
BASE = ROOT / "scripts" / "measure_search_issue370.py"
SUMMARY = ROOT / "docs" / "research" / "search_groups_issue437_summary.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure437_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure437_under_test"] = module
    spec.loader.exec_module(module)
    return module


def test_base_script_hash_recorded() -> None:
    module = _load()
    assert module.BASE_SCRIPT_SHA256 == hashlib.sha256(BASE.read_bytes()).hexdigest()
    assert module.HASHES  # те же закреплённые эталоны


def test_sha_is_required_and_validated(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    module = _load()
    out = tmp_path / "r.json"
    rc = module.main(["--repo", str(tmp_path), "--out", str(out), "--smoke-only", "--sha", "abc"])
    assert rc == 2
    assert "sha_must_be_full_hex" in capsys.readouterr().out
    assert not out.exists()


def _api() -> object:
    from types import SimpleNamespace

    from v8unpack_agent.modules import ModuleEntry, ModuleIndex
    from v8unpack_agent.search_contract import (
        ArtifactRef,
        OwnerLink,
        OwnerRef,
        SearchDocument,
        SearchDocumentSet,
    )
    from v8unpack_agent.search_corpus import CorpusReport, SearchCorpus

    return SimpleNamespace(
        ModuleEntry=ModuleEntry, ModuleIndex=ModuleIndex, ArtifactRef=ArtifactRef,
        OwnerRef=OwnerRef, OwnerLink=OwnerLink, SearchDocument=SearchDocument,
        SearchDocumentSet=SearchDocumentSet, SearchCorpus=SearchCorpus,
        CorpusReport=CorpusReport,
    )


def _form_artifacts() -> list[dict[str, object]]:
    data = json.loads(
        (ROOT / "docs" / "research" / "search_eval_issue329_paraphrase.json").read_text(encoding="utf-8")
    )
    return [a for a in data["artifacts"] if a["form_name"] is not None]


def test_layout_emulation_matches_corpus_rule() -> None:
    module = _load()
    forms = _form_artifacts()
    module.LAYOUT_SUPPORTED[0] = True
    corpus, _mapping = module.make_corpus(forms, _api())
    bases = {link.artifact.artifact_id: link.basis for link in corpus.owner_links}
    for artifact in forms:
        expected = "form_key" if artifact["owner_kind"] == "metadata_object_form" else "layout"
        assert bases[artifact["id"]] == expected
    for document in corpus.documents.documents:
        assert document.artifact.owner is not None
    module.LAYOUT_SUPPORTED[0] = False
    corpus, _mapping = module.make_corpus(forms, _api())
    assert {link.basis for link in corpus.owner_links} == {"form_key", "unconfirmed"}


def test_summary_is_consistent() -> None:
    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    runs = data["runs"]
    assert set(runs) == {"6d798c0", "5b6e1bd"}
    assert runs["6d798c0"]["layout_link_basis"] is False
    assert runs["5b6e1bd"]["layout_link_basis"] is True
    for run in runs.values():
        assert run["status"] == "evaluation_complete"
        groups = run["corpora"]["issue369"]["groups"]
        per_query = run["corpora"]["issue369"]["per_query"]
        for name in ("group:common_module:common_module", "group:form:external_report_form"):
            key = name.split(":", 1)[1]
            rows = [q for q in per_query.values() if q["group"] == key]
            assert len(rows) == groups[name]["n_free"] == 8
            recall = sum(1 for q in rows if q["gold_rank"] is not None and q["gold_rank"] <= 5) / 8
            assert round(recall, 4) == groups[name]["recall@5"]
    text = SUMMARY.read_text(encoding="utf-8")
    assert "/home/" not in text and "\\\\" not in text
