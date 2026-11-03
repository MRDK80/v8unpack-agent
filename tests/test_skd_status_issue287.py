"""Статусы СКД без текстового запроса и пакет с несколькими макетами — issue #287.

Синтетические ``Template.bin`` повторяют структуру реальных выгрузок:
двоичный префикс переменной длины (24/32/40 байт), UTF-8 BOM, XML с CRLF и
корнем ``SchemaFile``; вариант без префикса с корнем
``DataCompositionSchema``; макет другого вида в скобочном формате 1С.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_full_pipeline_walkthrough_issue175 import (  # bootstrap sys.path выше
    build_export,
    parse_steps,
)

from v8unpack_agent.skd_extractor import (
    STATUS_ERROR,
    STATUS_NOT_SCHEMA,
    STATUS_QUERY_EXTRACTED,
    STATUS_SCHEMA_WITHOUT_DATASETS,
    STATUS_SCHEMA_WITHOUT_QUERY,
    SkdResult,
    extract_all_skd_queries,
    extract_skd_queries,
)

BOM = b"\xef\xbb\xbf"
DECL = '<?xml version="1.0" encoding="UTF-8"?>\r\n'
NS = 'xmlns="" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "examples" / "full_pipeline_walkthrough.py"


def container(body: str, prefix: int = 24, root: str = "SchemaFile") -> bytes:
    xml = f"{DECL}<{root} {NS}>\r\n{body}\r\n</{root}>"
    head = b"\x00\x00\x00\x00\x01\x00\x00\x00" + b"\x00" * (prefix - 8) if prefix else b""
    return head + BOM + xml.encode("utf-8")


QUERY_SET = (
    '<dataSet xsi:type="DataSetQuery"><name>НаборЗапрос</name>'
    "<query>ВЫБРАТЬ Номенклатура.Ссылка КАК Ссылка ИЗ Справочник.Номенклатура</query>"
    "</dataSet>"
)
OBJECT_SET = '<dataSet xsi:type="DataSetObject"><name>НаборОбъект</name><objectName>Т</objectName></dataSet>'
UNION_SET = f'<dataSet xsi:type="DataSetUnion"><name>Объединение</name>{OBJECT_SET}</dataSet>'

SAMPLES = {
    "query": container(QUERY_SET),
    "query_prefix32": container(QUERY_SET, prefix=32),
    "query_no_prefix": container(QUERY_SET, prefix=0, root="DataCompositionSchema"),
    "object": container(OBJECT_SET),
    "object_prefix40": container(OBJECT_SET, prefix=40),
    "union": container(UNION_SET),
    "no_datasets": container("<dataSource><name>ИсточникДанных1</name></dataSource>"),
    "other_kind": BOM + b'{5,1,"ru",\r\n{#base64:77u/PEhUTUw+PEhFQUQ+}}',
    "other_xml_root": b"\x00" * 24 + BOM + (DECL + "<Moxcel/>").encode("utf-8"),
    "garbage": b"\x00" * 8 + b"not-xml-at-all",
    "query_tag_unparsed": container('<dataSet xsi:type="DataSetQuery"><query>SELECT 1</query></dataSet>'),
}

EXPECTED = {
    "query": STATUS_QUERY_EXTRACTED,
    "query_prefix32": STATUS_QUERY_EXTRACTED,
    "query_no_prefix": STATUS_QUERY_EXTRACTED,
    "object": STATUS_SCHEMA_WITHOUT_QUERY,
    "object_prefix40": STATUS_SCHEMA_WITHOUT_QUERY,
    "union": STATUS_SCHEMA_WITHOUT_QUERY,
    "no_datasets": STATUS_SCHEMA_WITHOUT_DATASETS,
    "other_kind": STATUS_NOT_SCHEMA,
    "other_xml_root": STATUS_NOT_SCHEMA,
    "garbage": STATUS_ERROR,
    "query_tag_unparsed": STATUS_ERROR,
}


def put(root: Path, obj: str, template: str, data: bytes) -> Path:
    path = root / "Catalog" / obj / "Template" / template / "Template.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_single_template_status(tmp_path: Path, kind: str) -> None:
    put(tmp_path, "Объект", "Макет", SAMPLES[kind])
    result = extract_skd_queries(tmp_path / "Catalog" / "Объект")
    assert result.status == EXPECTED[kind]
    assert result.skd_extracted is (EXPECTED[kind] == STATUS_QUERY_EXTRACTED)
    assert bool(result.datasets) is result.skd_extracted
    written = (tmp_path / "Catalog" / "Объект" / "skd_queries.json").exists()
    assert written is result.skd_extracted


def test_status_defaults_follow_skd_extracted() -> None:
    assert SkdResult(skd_extracted=True).status == STATUS_QUERY_EXTRACTED
    assert SkdResult(skd_extracted=False).status == STATUS_ERROR


def test_batch_parses_every_template_of_one_object(tmp_path: Path) -> None:
    put(tmp_path, "Объект", "М1", SAMPLES["object"])
    put(tmp_path, "Объект", "М2", SAMPLES["query"])
    put(tmp_path, "Объект", "М3", container(QUERY_SET.replace("НаборЗапрос", "Второй").replace("Номенклатура", "Склады")))

    batch = extract_all_skd_queries(tmp_path)

    assert [r.status for r in batch.results] == [
        STATUS_SCHEMA_WITHOUT_QUERY,
        STATUS_QUERY_EXTRACTED,
        STATUS_QUERY_EXTRACTED,
    ]
    assert len({r.template_path for r in batch.results}) == 3
    saved = json.loads((tmp_path / "Catalog" / "Объект" / "skd_queries.json").read_text(encoding="utf-8"))
    assert [d["query"] for d in saved] == [d["query"] for r in batch.results for d in r.datasets]
    assert len(saved) == 2


def test_batch_mixed_classes_keep_exact_counts(tmp_path: Path) -> None:
    for i, kind in enumerate(sorted(SAMPLES)):
        put(tmp_path, f"О{i:02d}", "Макет", SAMPLES[kind])

    batch = extract_all_skd_queries(tmp_path)

    got = sorted(r.status for r in batch.results)
    assert got == sorted(EXPECTED[k] for k in SAMPLES)
    assert sum(r.skd_extracted for r in batch.results) == 3
    assert batch.skd_extracted is True


def run_walkthrough(root: Path) -> dict[str, str]:
    build_export(root)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "--skip-common-modules"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return parse_steps(proc.stdout)[10]


def test_walkthrough_without_errors_is_ok(tmp_path: Path) -> None:
    for i, kind in enumerate(["query", "object", "union", "no_datasets", "other_kind"]):
        put(tmp_path, f"О{i}", "Макет", SAMPLES[kind])
    step = run_walkthrough(tmp_path)
    assert step["status"] == "ok"
    assert step["success"] == "1"
    assert step["schema_without_query"] == "2"
    assert step["schema_without_datasets"] == "1"
    assert step["not_schema"] == "1"
    assert step["error"] == "0"


def test_walkthrough_proven_error_is_degraded(tmp_path: Path) -> None:
    put(tmp_path, "О1", "Макет", SAMPLES["object"])
    put(tmp_path, "О2", "Макет", SAMPLES["garbage"])
    step = run_walkthrough(tmp_path)
    assert step["status"] == "degraded"
    assert step["error"] == "1"
