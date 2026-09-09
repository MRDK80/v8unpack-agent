"""Падающие синтетические тесты идентичности форм (issue #226, шаг 1).

Набор фиксирует требования до реализации: уникальная идентичность формы,
collision-free каталоги результата, обезличенный и платформенно-независимый
JSON индекса. До появления нового контракта discovery тесты обязаны падать —
это точка отсчёта (red) для #226.

Импорты новых символов выполняются внутри тестов, чтобы отсутствие контракта
не ломало сбор всего набора тестов.
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKSLASH = chr(92)


def _make_colliding_dump(tmp_path: Path) -> Path:
    """Выгрузка, где одно имя формы принадлежит двум разным владельцам."""
    dump = tmp_path / "dump"
    for owner in ("ОбъектПервый", "ОбъектВторой"):
        ext = dump / "Catalog" / owner / "Forms" / "ФормаСписка" / "Ext"
        ext.mkdir(parents=True)
        (ext / "Form.bin").write_bytes(b"\x00binary\x00")
    return dump


def _make_simple_dump(tmp_path: Path) -> Path:
    dump = tmp_path / "dump"
    ext = dump / "Catalog" / "ОбъектПервый" / "Forms" / "ФормаЭлемента" / "Ext"
    ext.mkdir(parents=True)
    (ext / "Form.bin").write_bytes(b"\x00binary\x00")
    return dump


def _source_unpacker(source, unpacked_root: Path):
    """Заглушка нового протокола: (FormBinSource, unpacked_root) -> FormArtifact."""
    from v8unpack_agent import FormArtifact

    target = unpacked_root / "Form" / Path(source.form_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / "Form.obj.bsl").write_text("// форма", encoding="utf-8")
    return FormArtifact.for_source(unpacked_root, source)


def test_discovery_keeps_every_form_bin(tmp_path):
    """Ни один Form.bin не теряется при совпадении имён форм."""
    from v8unpack_agent import discover_form_sources

    dump = _make_colliding_dump(tmp_path)
    sources = discover_form_sources(dump)

    assert len(sources) == 2
    assert {s.form_name for s in sources} == {"ФормаСписка"}
    assert len({s.form_id for s in sources}) == 2


def test_form_id_is_relative_and_posix(tmp_path):
    """form_id — относительный POSIX-ключ без разделителей Windows."""
    from v8unpack_agent import discover_form_sources

    dump = _make_simple_dump(tmp_path)
    (source,) = discover_form_sources(dump)

    assert not Path(source.form_id).is_absolute()
    assert BACKSLASH not in source.form_id
    assert source.form_id.startswith("Catalog/ОбъектПервый/")
    assert source.form_id.endswith("ФормаЭлемента")


def test_form_id_is_stable_across_runs(tmp_path):
    """Повторное обнаружение даёт тот же form_id."""
    from v8unpack_agent import discover_form_sources

    dump = _make_simple_dump(tmp_path)
    first = [s.form_id for s in discover_form_sources(dump)]
    second = [s.form_id for s in discover_form_sources(dump)]

    assert first == second


def test_source_carries_owner_and_bin_path(tmp_path):
    """Источник несёт владельца и физический путь, а не только имя."""
    from v8unpack_agent import discover_form_sources

    dump = _make_simple_dump(tmp_path)
    (source,) = discover_form_sources(dump)

    assert source.bin_path.name == "Form.bin"
    assert source.owner_name == "ОбъектПервый"
    assert source.owner_kind == "Catalog"


def test_output_dirs_do_not_collide(tmp_path):
    """Одноимённые формы разных владельцев не пишут в один каталог."""
    from v8unpack_agent import discover_form_sources, unpack_all_forms

    dump = _make_colliding_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    sources = discover_form_sources(dump)

    artifacts = unpack_all_forms(dump, unpacked, _source_unpacker)

    assert len(artifacts) == len(sources) == 2
    roots = {a.paths["object_module"].parent for a in artifacts}
    assert len(roots) == 2
    modules = sorted(unpacked.rglob("Form.obj.bsl"))
    assert len(modules) == 2


def test_artifact_keeps_form_id_and_source(tmp_path):
    """FormArtifact связан с источником без повторного discovery."""
    from v8unpack_agent import discover_form_sources, unpack_all_forms

    dump = _make_simple_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    (source,) = discover_form_sources(dump)

    (artifact,) = unpack_all_forms(dump, unpacked, _source_unpacker)

    assert artifact.form_id == source.form_id
    assert artifact.name == "ФормаЭлемента"
    assert artifact.source is not None
    assert artifact.source.bin_path == source.bin_path


def test_index_is_keyed_by_form_id(tmp_path):
    """Индекс хранит обе одноимённые формы под разными ключами."""
    from v8unpack_agent import (
        discover_form_sources,
        unpack_all_forms,
        update_forms_index,
    )

    dump = _make_colliding_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    sources = discover_form_sources(dump)

    artifacts = unpack_all_forms(dump, unpacked, _source_unpacker)
    idx = update_forms_index(dump, unpacked, artifacts)

    assert set(idx.entries()) == {s.form_id for s in sources}
    for source in sources:
        entry = idx.get(source.form_id)
        assert entry is not None
        assert entry.form_name == "ФормаСписка"


def test_index_json_has_no_absolute_paths(tmp_path):
    """Индекс остаётся обезличенным: абсолютных путей в нём нет."""
    import json

    from v8unpack_agent import unpack_all_forms, update_forms_index

    dump = _make_simple_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    artifacts = unpack_all_forms(dump, unpacked, _source_unpacker)
    idx = update_forms_index(dump, unpacked, artifacts)

    index_path = idx.save(tmp_path / "forms_index.json")
    raw = json.loads(index_path.read_text(encoding="utf-8"))

    for row in raw["forms"].values():
        assert not Path(row["bin_path"]).is_absolute()
        assert not Path(row["unpacked_root"]).is_absolute()
        assert BACKSLASH not in row["bin_path"]
        assert BACKSLASH not in row["unpacked_root"]
    assert str(tmp_path) not in index_path.read_text(encoding="utf-8")


def test_index_json_declares_schema_version(tmp_path):
    """JSON индекса объявляет версию схемы для миграции legacy-файлов."""
    import json

    from v8unpack_agent import unpack_all_forms, update_forms_index

    dump = _make_simple_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    artifacts = unpack_all_forms(dump, unpacked, _source_unpacker)
    idx = update_forms_index(dump, unpacked, artifacts)

    raw = json.loads(
        idx.save(tmp_path / "forms_index.json").read_text(encoding="utf-8")
    )

    assert int(raw["schema_version"]) >= 2
    assert "forms" in raw


def test_legacy_flat_index_is_migrated(tmp_path):
    """Legacy-индекс без schema_version читается и миграция не теряет записи."""
    import json

    from v8unpack_agent import FormsIndex

    legacy = {
        "ФормаЭлемента": {
            "bin_path": "Catalog/ОбъектПервый/Forms/ФормаЭлемента/Ext/Form.bin",
            "unpacked_root": "unpacked/Form/ФормаЭлемента",
            "bin_mtime": 1.0,
            "unpacked_mtime": 2.0,
            "extraction_ok": True,
            "warnings": [],
        }
    }
    index_path = tmp_path / "forms_index.json"
    index_path.write_text(
        json.dumps(legacy, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    idx = FormsIndex.load(index_path)
    entries = idx.entries()

    assert len(entries) == 1
    (entry,) = entries.values()
    assert entry.form_name == "ФормаЭлемента"
    assert entry.form_id


def test_filter_by_form_ids_is_canonical(tmp_path):
    """Отбор идёт по form_id; неоднозначное имя не выбирает произвольную форму."""
    from v8unpack_agent import discover_form_sources, unpack_all_forms

    dump = _make_colliding_dump(tmp_path)
    unpacked = tmp_path / "unpacked"
    wanted = min(s.form_id for s in discover_form_sources(dump))

    artifacts = unpack_all_forms(
        dump, unpacked, _source_unpacker, form_ids=[wanted]
    )

    assert [a.form_id for a in artifacts] == [wanted]


def test_ambiguous_legacy_name_is_reported(tmp_path):
    """Легаси-отбор по имени при коллизии не молчит."""
    from v8unpack_agent import AmbiguousFormNameError, unpack_all_forms

    dump = _make_colliding_dump(tmp_path)
    unpacked = tmp_path / "unpacked"

    with pytest.raises(AmbiguousFormNameError) as excinfo:
        unpack_all_forms(
            dump, unpacked, _source_unpacker, form_names=["ФормаСписка"]
        )

    assert "ambiguous_legacy_name" in str(excinfo.value)


def test_legacy_unpacker_needs_explicit_adapter(tmp_path):
    """Старый (bin_path, root, name) распаковщик работает только через адаптер."""
    from v8unpack_agent import FormArtifact, adapt_legacy_unpacker, unpack_all_forms

    dump = _make_simple_dump(tmp_path)
    unpacked = tmp_path / "unpacked"

    def legacy(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
        target = root / "Form" / form_name
        target.mkdir(parents=True, exist_ok=True)
        (target / "Form.obj.bsl").write_text("// форма", encoding="utf-8")
        return FormArtifact.for_form(root, form_name)

    with pytest.deprecated_call():
        adapted = adapt_legacy_unpacker(legacy)

    (artifact,) = unpack_all_forms(dump, unpacked, adapted)
    assert artifact.form_id
    assert artifact.name == "ФормаЭлемента"


def test_output_key_rejects_traversal(tmp_path):
    """form_id с выходом за пределы unpacked_root отклоняется явно."""
    from v8unpack_agent import FormIdentityError, form_root

    with pytest.raises(FormIdentityError):
        form_root(tmp_path / "unpacked", "../снаружи/ФормаСписка")
