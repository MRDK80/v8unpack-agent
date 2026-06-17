"""Пример: распаковка обычных форм 1С как pre-step индексации.

Пример полностью синтетический и самодостаточный: вместо реального v8unpack
используется распаковщик-заглушка, который пишет текстовый файл рядом. Реальные
данные, контейнеры 1С и внутренняя инфраструктура не используются.

Запуск:

    python examples/basic_usage.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from v8unpack_agent import (
    FormArtifact,
    form_paths,
    is_form_stale,
    unpack_all_forms,
    update_forms_index,
)


def make_demo_dump(dump_root: Path, *form_names: str) -> None:
    """Создать синтетическую выгрузку с .../Forms/<имя>/Ext/Form.bin."""
    for name in form_names:
        ext = dump_root / "Catalog" / "Номенклатура" / "Forms" / name / "Ext"
        ext.mkdir(parents=True, exist_ok=True)
        (ext / "Form.bin").write_bytes(b"\x00demo-binary\x00")


def demo_unpacker(bin_path: Path, unpacked_root: Path, form_name: str) -> FormArtifact:
    """Распаковщик-заглушка вместо v8unpack.extract(...).

    Пишет Form.obj.bsl по конвенции и возвращает FormArtifact. Форму с именем
    «ФормаСписка» намеренно делаем частичной, чтобы показать обработку
    extraction_ok=False без падения пайплайна.
    """
    target = unpacked_root / "Form" / form_name
    target.mkdir(parents=True, exist_ok=True)
    (target / "Form.obj.bsl").write_text("// демо-код формы", encoding="utf-8")

    if form_name == "ФормаСписка":
        return FormArtifact.for_form(
            unpacked_root, form_name,
            extraction_ok=False,
            extraction_warnings=["вложенная панель не распакована"],
        )
    return FormArtifact.for_form(unpacked_root, form_name)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        dump_root = base / "dump"
        unpacked_root = base / "text_layer"

        make_demo_dump(dump_root, "ФормаЭлемента", "ФормаСписка")

        # 1) распаковываем все формы выгрузки
        artifacts = unpack_all_forms(dump_root, unpacked_root, demo_unpacker)
        print("Распаковано форм:", len(artifacts))
        for art in artifacts:
            flag = "ok" if art.extraction_ok else f"частично: {art.extraction_warnings}"
            print(f"  - {art.name}: {flag}")
            print(f"    object_module = {art.paths['object_module']}")

        # 2) строим карту актуальности
        index = update_forms_index(dump_root, unpacked_root, artifacts)
        index_path = unpacked_root / "forms_index.json"
        index.save(index_path)
        print("\nforms_index сохранён:", index_path)

        # 3) проверяем свежесть
        print("Устаревшие формы:", index.stale_forms() or "нет")
        entry = index.get("ФормаЭлемента")
        print("ФормаЭлемента устарела?", is_form_stale(entry))

        # ручной разбор путей одной формы
        paths = form_paths(unpacked_root, "ФормаЭлемента")
        print("\nКонвенция путей для ФормаЭлемента:")
        for key, value in paths.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
