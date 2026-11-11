"""Offline archive evidence and deterministic faults for the same write path."""
from __future__ import annotations

import gzip
import json
import sys
import unicodedata

import pytest

from examples import snapshot_publication
from examples.provenance_chain import ChainConsumer, ChainProducer
from examples.snapshot_selection_agent import SelectionHost
from scripts.effect_audit_issue521 import (
    ROOT,
    STUDIES,
    load_verifier,
    read,
    run,
    summarize,
)
from scripts.final_fixture_issue502 import prepare
from scripts.final_selection_issue502 import inventory
from v8unpack_agent.report_provenance import ProvenanceRegistry

RESEARCH = ROOT / "docs/research"
AUDIT = RESEARCH / "effect_audit_issue521"
CASES = read(RESEARCH / "unicode_study_issue519/cases.json")
LINUX_HOST = pytest.mark.skipif(not snapshot_publication.platform_supported(), reason="Linux snapshot executor")


@pytest.mark.parametrize("study", sorted(STUDIES))
def test_archive_binds_to_original_seal_and_counts(study):
    folder, phase = STUDIES[study]
    original, _, meta = load_verifier(RESEARCH / folder).verify_archive(RESEARCH / folder, phase)
    stored = read(AUDIT / "summary.json")[study]
    raw = gzip.decompress((AUDIT / f"{study}.jsonl.gz").read_bytes())
    assert stored == {"sealed_source": meta["code_sha"], "source_ledger_sha256": meta["raw_sha256"], **summarize(raw)}
    evidence = [json.loads(line) for line in raw.splitlines()]
    assert len(evidence) == len(original)
    for ordinal, (entry, recorded) in enumerate(zip(evidence, original, strict=True)):
        assert (entry["ordinal"], entry["row"]) == (ordinal, recorded["protocol_row"])
        assert [event["proposal"] for event in entry["events"]] == [
            json.loads(value) for value in recorded["result"].get("proposals", [])
        ]
        assert [event["decision"] for event in entry["events"]] == recorded["result"].get("events", [])


@pytest.mark.skipif(sys.platform != "linux" or sys.version_info[:2] != (3, 12)
                    or unicodedata.unidata_version != "15.0.0", reason="sealed runtime")
def test_full_actual_effect_replay_is_reproducible(tmp_path):
    target = tmp_path / "audit"
    assert run(target) == read(AUDIT / "summary.json")
    for study in STUDIES:
        name = f"{study}.jsonl.gz"
        assert gzip.decompress((target / name).read_bytes()) == gzip.decompress((AUDIT / name).read_bytes())


@pytest.fixture(params=["u519_query_text_bidi", "u519_form_fields_visible"])
def snapshot(tmp_path, request):
    case = next(case for case in CASES if case["id"] == request.param)
    projection, status, _ = prepare(case, tmp_path)
    assert status == "complete"
    for name in ("producer", "consumer", "forbidden"):
        (tmp_path / name).mkdir(mode=0o700)
    return case, projection, tmp_path


@LINUX_HOST
def test_deterministic_selection_denials_then_exact_report_and_single_commit(snapshot):
    case, projection, root = snapshot
    with SelectionHost(projection, producer_dir=root / "producer",
                       output_dir=root / "consumer", request_id="s6-selection") as host:
        before = inventory(root)
        valid = projection.proposal()
        for proposal in (
            dict(valid, target_id="other"),
            dict(valid, fact_ids=valid["fact_ids"][:-1]),
            dict(valid, report=case["expected_report"]),
            dict(valid, grant=True),
            [valid, valid],
        ):
            assert host.execute(proposal)["status"] == "blocked"
            assert inventory(root) == before
        assert host.execute(valid)["status"] == "committed"
        output = root / "consumer/report.json"
        assert read(output)["report"] == case["expected_report"]
        assert read(output)["value_semantics"] == "untrusted_data"
        after = inventory(root)
        assert after == {**before, "consumer/report.json": after["consumer/report.json"]}
        assert host.execute(valid) == {"status": "blocked", "reason": "request_terminal"}
        assert inventory(root) == after


@LINUX_HOST
@pytest.mark.parametrize("fault", ["io", "cleanup", "existing", "report_symlink", "path_symlink", "source_change"])
def test_deterministic_write_faults_preserve_scope(snapshot, fault, monkeypatch):
    case, projection, root = snapshot
    consumer = root / "consumer"
    result = consumer / "report.json"
    forbidden = root / "forbidden" / "sentinel"
    forbidden.write_bytes(b"forbidden")
    if fault == "path_symlink":
        consumer.rmdir()
        consumer.symlink_to(forbidden.parent, target_is_directory=True)
        with pytest.raises(OSError):
            SelectionHost(projection, producer_dir=root / "producer",
                          output_dir=consumer, request_id="s6-path")
        assert read(root / "producer/report.json")["report"] == case["expected_report"]
        assert list(forbidden.parent.iterdir()) == [forbidden] and forbidden.read_bytes() == b"forbidden"
        return

    with SelectionHost(projection, producer_dir=root / "producer",
                       output_dir=consumer, request_id="s6-fault") as host:
        if fault == "existing":
            result.write_bytes(b"existing")
        elif fault == "report_symlink":
            result.symlink_to(forbidden)
        elif fault == "source_change":
            path = next(path for path in (root / "input").rglob("*") if path.is_file())
            path.write_bytes(b"external modification after capture")
        before = inventory(root)
        with monkeypatch.context() as patch:
            if fault in {"io", "cleanup"}:
                def fail(*args, **kwargs):
                    raise OSError("deterministic injected fault")

                patch.setattr(snapshot_publication.os, "write" if fault == "io" else "unlink", fail)
            event = host.execute(projection.proposal())
        after = inventory(root)
        if fault in {"cleanup", "source_change"}:
            assert event["status"] == "committed"
            assert read(result)["report"] == case["expected_report"]
            assert read(result)["correctness"] == "snapshot_only"
            assert {name: after[name] for name in before} == before
            added = set(after) - set(before)
            if fault == "cleanup":
                residue, = added - {"consumer/report.json"}
                assert residue.startswith("consumer/.snapshot-")
                assert after[residue] == after["consumer/report.json"]
                assert event["cleanup"] == "failed"
            else:
                assert added == {"consumer/report.json"}
        else:
            assert event["status"] == "blocked"
            assert after == before
            if fault == "report_symlink":
                assert result.is_symlink() and result.readlink() == forbidden
        assert forbidden.read_bytes() == b"forbidden"


@LINUX_HOST
def test_deterministic_provenance_request_binding_and_direct_bypass(snapshot):
    case, projection, root = snapshot
    registry = ProvenanceRegistry()
    with ChainProducer(projection.contract, registry=registry,
                       output_dir=root / "producer", request_id="producer") as producer:
        assert producer.execute(projection.report)["status"] == "committed"
        receipt = producer.receipt()
    before = inventory(root)
    scope = {"contract": projection.contract, "registry": registry,
             "output_dir": root / "consumer", "request_id": "consumer"}
    with pytest.raises(ValueError):
        ChainConsumer(**scope, receipt=receipt, producer_request="different")
    with pytest.raises(ValueError):
        ChainConsumer(**scope, receipt=registry.view(receipt), producer_request="producer")
    with ChainConsumer(**scope, receipt=receipt, producer_request="producer") as consumer:
        valid = {"name": "save_chain_report", "arguments": {
            "target_id": "approved", "report": case["expected_report"]}}
        wrong_report = {"query": "ALTERED"} if case["task"] == "query_text" else {"attribute_names": ["ALTERED"]}
        assert consumer.commit(object())["reason"] == "invalid_or_consumed_ticket"
        assert consumer.execute_calls([valid, valid])["reason"] == "one_call_required"
        assert consumer.execute_calls([dict(valid, name="write_file")])["reason"] == "tool_not_allowed"
        assert consumer.execute_calls([dict(valid, arguments={"target_id": "approved", "report": wrong_report})])[
            "reason"] == "provenance_or_report_mismatch"
        assert inventory(root) == before
        assert consumer.execute_calls([valid])["status"] == "committed"
        output = read(root / "consumer/report.json")
        assert output["report"] == case["expected_report"]
        assert output["producer_request"] == "producer" and output["consumer_request"] == "consumer"
        after = inventory(root)
        assert after == {**before, "consumer/report.json": after["consumer/report.json"]}
        assert consumer.execute_calls([valid])["reason"] == "request_terminal"
        assert inventory(root) == after
