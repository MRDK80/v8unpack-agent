"""Actual package/request boundaries with synthetic image canaries, no network."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from test_output_characterization_issue456 import _make_export

from examples.agent_reference_client import PROFILES, SERVER_VERSION
from examples.bounded_fact_agent import TimedLocalOllama
from scripts.final_selection_issue502 import request as selection_request
from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.search_api import collect_search_corpus, search_with_context
from v8unpack_agent.search_fragments import fragment_corpus

FIXTURES = Path(__file__).parent / 'fixtures_issue518'
METADATA = 'METADATA_SENTINEL_518'


class TransportTap:
    """A fake HTTP boundary, never a real model or a runtime admission claim."""
    def __init__(self):
        self.requests = []

    def open(self, request, *, timeout):
        path = request.full_url.rsplit('/', 1)[-1]
        if path == 'version':
            result = {'version': SERVER_VERSION}
        elif path == 'tags':
            result = {'models': [{'name': p['model'], 'digest': p['digest']} for p in PROFILES.values()]}
        else:
            assert path == 'chat'
            assert 0 < timeout <= 180
            assert request.get_header('Content-type') == 'application/json'
            self.requests.append(json.loads(request.data))
            result = {'message': {'role': 'assistant', 'content': '{}'}}
        return io.BytesIO(json.dumps(result).encode('utf-8'))


def transport():
    tap = TransportTap()
    client = TimedLocalOllama()
    client._opener = tap
    client.verify('qwen25')  # Fake metadata only; the network is never reached.
    return client, tap


@pytest.mark.parametrize('suffix', ['png', 'jpg'])
def test_fixtures_are_pinned_synthetic_bytes_not_model_acceptance_cases(suffix):
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))
    name = 'probe.' + suffix
    raw = (FIXTURES / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['files'][name]['sha256']
    assert len(raw) == manifest['files'][name]['bytes'] < 2_097_152
    assert METADATA.encode('ascii') in raw
    assert manifest['files'][name]['size'] == [420, 96]
    assert raw.startswith(b'\x89PNG\r\n\x1a\n' if suffix == 'png' else b'\xff\xd8\xff')
    assert manifest['scope'] == 'synthetic route canaries, not model acceptance inputs'


@pytest.mark.parametrize('suffix', ['png', 'jpg'])
@pytest.mark.parametrize('representation', ['legacy', 'framed'])
def test_normal_package_context_and_actual_transport_do_not_auto_attach_images(tmp_path, monkeypatch, suffix, representation):
    root = _make_export(tmp_path)
    before_corpus = collect_search_corpus(root).to_json()
    raw = (FIXTURES / ('probe.' + suffix)).read_bytes()
    image = root / 'CommonPicture' / 'Canary' / 'CommonPicture.bin'
    image.parent.mkdir(parents=True)
    image.write_bytes(raw)
    metadata = image.with_suffix('.json')
    metadata.write_text(json.dumps({'header': {'comment': METADATA}}), encoding='utf-8')
    original_open = Path.open

    def no_image_read(path, *args, **kwargs):
        if path == image:
            raise AssertionError('default package entry point opened image bytes')
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_image_read)
        form = scan_forms(root).forms[0]
        form_context = to_llm_prompt_fragment(build_form_context(form, root), representation=representation)
        corpus = collect_search_corpus(root)
        assert corpus.to_json() == before_corpus
        view = search_with_context(fragment_corpus(corpus), 'SharedHelpers', max_chars=-1,
                                   representation=representation)
        content = view.to_json(include_text=True)
    assert METADATA not in content + form_context
    assert 'CommonPicture.bin' not in content + form_context
    assert base64.b64encode(raw).decode('ascii') not in content + form_context
    body = selection_request({'profile': 'qwen25', 'seed': 17, 'variant': 'legacy_guard',
                              'task': 'query_text'}, content, [], None)
    client, tap = transport()
    client.chat(body)
    assert tap.requests == [body]
    assert all('images' not in m for m in tap.requests[0]['messages'])
    assert image.read_bytes() == raw


@pytest.mark.parametrize('suffix', ['png', 'jpg'])
def test_explicit_host_image_bytes_reach_generic_transport_not_automatic_vision(suffix):
    raw = (FIXTURES / ('probe.' + suffix)).read_bytes()
    client, tap = transport()
    # The fake text-model name proves only transport forwarding. A real host
    # must separately verify a vision model/capability before generation.
    body = {'model': PROFILES['qwen25']['model'], 'stream': False,
            'messages': [{'role': 'user', 'content': 'Synthetic route probe',
                          'images': [base64.b64encode(raw).decode('ascii')]}]}
    client.chat(body)
    observed = tap.requests[0]['messages'][0]['images'][0]
    assert base64.b64decode(observed, validate=True) == raw
    assert METADATA.encode('ascii') in base64.b64decode(observed)
    # No assertion that image metadata/LSB reached an encoder's representation.


def test_explicit_ocr_or_metadata_text_is_untrusted_message_data():
    client, tap = transport()
    body = selection_request({'profile': 'qwen25', 'seed': 17, 'variant': 'minimal_guard',
                              'task': 'query_text'}, METADATA, [], None)
    client.chat(body)
    messages = tap.requests[0]['messages']
    assert messages[2] == {'role': 'tool', 'content': METADATA}
    assert METADATA not in messages[0]['content']
    assert 'images' not in messages[2]
    assert body['format']['additionalProperties'] is False


def test_image_reference_or_metadata_text_copied_to_bsl_can_reach_text_context(tmp_path):
    root = _make_export(tmp_path)
    module = root / 'CommonModule' / 'SharedHelpers' / 'CommonModule.obj.bsl'
    raw = module.read_bytes()
    module.write_bytes(('// ' + METADATA + '\n').encode('utf-8') + raw)
    corpus = collect_search_corpus(root)
    view = search_with_context(fragment_corpus(corpus), METADATA, max_chars=-1)
    content = view.to_json(include_text=True)
    assert METADATA in content
    body = selection_request({'profile': 'qwen25', 'seed': 17, 'variant': 'legacy_guard',
                              'task': 'query_text'}, content, [], None)
    client, tap = transport()
    client.chat(body)
    assert METADATA in tap.requests[0]['messages'][2]['content']
    assert METADATA not in tap.requests[0]['messages'][0]['content']


@pytest.mark.parametrize('representation', ['legacy', 'framed'])
def test_actual_cli_runner_does_not_read_picture_bytes(tmp_path, monkeypatch, capsys, representation):
    from v8unpack_agent import cli, runner

    root = _make_export(tmp_path)
    monkeypatch.setattr(runner, '_utc_now', lambda: '2026-01-01T00:00:00.000000Z')
    before = tmp_path / 'before.json'
    options = ['--skip-skd', '--representation', representation]
    baseline_exit = cli.main([str(root), '--report-path', str(before), *options])
    baseline = before.read_bytes()
    raw = (FIXTURES / 'probe.png').read_bytes()
    image = root / 'CommonPicture' / 'Canary' / 'CommonPicture.bin'
    image.parent.mkdir(parents=True)
    image.write_bytes(raw)
    after = tmp_path / 'after.json'
    original_open = Path.open

    def no_image_read(path, *args, **kwargs):
        if path == image:
            raise AssertionError('CLI opened image bytes')
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_image_read)
        assert cli.main([str(root), '--report-path', str(after), *options]) == baseline_exit
    assert after.read_bytes() == baseline
    assert image.read_bytes() == raw
    capsys.readouterr()
