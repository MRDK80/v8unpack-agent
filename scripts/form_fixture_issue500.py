"""Synthetic files in the real v8unpack managed props layout; no real CF data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def fixture_pair(names: list[str], *, root_version: str = '4') -> tuple[dict[str, Any], dict[str, Any]]:
    props = []
    for index, name in enumerate(names, 1):
        ident = str(index)
        raw = ['9', [ident], '0', '"' + name + '"', '0', [],
               '0', '0', '0', '0', '0', '0', '0', '0', '0', '0']
        props.append({'name': name, 'id': ident, 'raw': raw})
    root = [root_version, [], [],
            ['0', 'Дочерние элементы отдельно' if names else '0', '0', '0', '0'], []]
    metadata = {'name': 'SyntheticForm', 'header': [], 'Тип формы': '1',
                'Версия элементов формы': '1', 'form': [[root]]}
    elements = {'params': None, 'props': props or None, 'commands': None, 'tree': [], 'data': {}}
    return metadata, elements


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def write_fixture(root: Path, names: list[str], *, form_name: str = 'SyntheticForm') -> tuple[Path, Path]:
    folder = root / 'CommonForm' / form_name
    folder.mkdir(parents=True)
    metadata, elements = fixture_pair(names)
    metadata['name'] = form_name
    meta_path, elem_path = folder / 'CommonForm.json', folder / 'CommonForm.elem.json'
    meta_path.write_bytes(encoded(metadata))
    elem_path.write_bytes(encoded(elements))
    return meta_path, elem_path
