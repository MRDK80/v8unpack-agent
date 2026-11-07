"""Published real model results must replay offline, preserving failures and NO-GO."""
import unicodedata


def test_real_archive_cross_unicode_replay_preserves_scope_and_negative_results(monkeypatch):
    from scripts.audit_acceptance_results_issue485 import audit
    monkeypatch.setattr(unicodedata, 'unidata_version', '14.0.0')
    summary = audit()
    groups = summary['groups']
    assert summary['rows'] == 5313
    assert sum(g['wrong_commits'] for k, g in groups.items() if k.endswith(':legacy')) == 198
    assert sum(g['wrong_commits'] for k, g in groups.items() if k.endswith(':framed')) == 199
    guarded = [g for k, g in groups.items() if k.endswith(('_guard', ':actual_native', ':deterministic'))]
    assert all(g['wrong_commits'] == 0 for g in guarded)
    admitted = {k for k, g in groups.items() if g['actual_native_acceptance_go']}
    assert admitted == {'qwen25:query_text:actual_native', 'qwen3_off4096:query_text:actual_native'}
    assert groups['llama31:query_text:actual_native']['model_requested'] == 0
    assert groups['qwen25:form_fields:evidence_guard']['unsupported'] == 20
    assert groups['none:form_fields:deterministic']['correct'] == 0
    assert sum(g['correct'] for k, g in groups.items() if k.endswith(':reader')) == 463
    assert summary['faults']['external_currentness_counterexamples'] == 1
