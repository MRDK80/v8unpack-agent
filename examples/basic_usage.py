"""Пример: распаковка обычных форм 1С как pre-step индексации.

Пример полностью синтетический и самодостаточный: вместо реального v8unpack
используется распаковщик-заглушка, который пишет текстовый файл рядом. Реальные
данные, контейнеры 1С и внутренняя инфраструктура не используются.

Запуск:

    python examples/basic_usage.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v8unpack_agent import (
    FormArtifact,
    form_paths,
    is_form_stale,
    unpack_all_forms,
    update_forms_index,
)
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.drift_checker import check_drift


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


def make_demo_external(external_root: Path) -> None:
    """Создать синтетическую выгрузку внешних объектов (mode=\"external\").

    Демонстрирует обе схемы v8unpack 1.2.11 (issue #32):
    - обработка: контейнер Form/, форма Form.obj.bsl, модуль
      ExternalDataProcessor.obj.bsl → object_type=ExternalDataProcessor;
    - отчёт: контейнер ReportForm/, форма ReportForm.obj.bsl →
      object_type=ExternalReport (определяется по контейнеру).
    """
    # Обработка.
    proc = external_root / "ЗагрузкаЦен"
    (proc / "Form" / "Форма").mkdir(parents=True, exist_ok=True)
    (proc / "ExternalDataProcessor.obj.bsl").write_text(
        "// демо-модуль объекта обработки", encoding="utf-8"
    )
    (proc / "Form" / "Форма" / "Form.obj.bsl").write_text(
        "// демо-код формы обработки", encoding="utf-8"
    )
    (proc / "Form" / "Форма" / "Form.json").write_text("{}", encoding="utf-8")

    # Отчёт.
    report = external_root / "СводныйОтчёт"
    (report / "ReportForm" / "ФормаОтчёта").mkdir(parents=True, exist_ok=True)
    (report / "ReportForm" / "ФормаОтчёта" / "ReportForm.obj.bsl").write_text(
        "// демо-код формы отчёта", encoding="utf-8"
    )
    (report / "ReportForm" / "ФормаОтчёта" / "Form.json").write_text(
        "{}", encoding="utf-8"
    )


def make_demo_config_forms(config_root: Path) -> None:
    """Создать синтетическую выгрузку форм конфигурации (mode=\"config\").

    Демонстрирует drift-детекцию по bsl_sha256 (issue #38) и elem_sha256
    (issue #40): baseline → изменение кода → изменение разметки.
    """
    form_dir = config_root / "Catalog" / "Товары" / "Forms" / "ФормаЭлемента"
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "Form.obj.bsl").write_text("// исходный код", encoding="utf-8")
    # Минимальный elem.json: нормализованный индекс будет содержать один элемент
    elem_data = [
        {"name": "Наименование", "type": "InputField", "path": "Наименование",
         "parent": None, "parent_path": None, "page": None,
         "source": "Наименование", "data_path": "Наименование", "handler": None,
         # косметические поля, которые elem_sha256 игнорирует:
         "left": 10, "top": 20, "color": "#000000"}
    ]
    (form_dir / "Form.elem.json").write_text(
        json.dumps(elem_data, ensure_ascii=False), encoding="utf-8"
    )


def demo_drift(config_root: Path) -> None:
    """Демонстрация drift-детекции: bsl_sha256 (#38) и elem_sha256 (#40)."""
    # Baseline-сканирование.
    baseline = scan_forms(config_root, mode="config")
    baseline_path = config_root / "forms_scan_index_baseline.json"
    baseline.save(baseline_path)
    print("\n[drift] baseline сохранён:", baseline_path)
    for e in baseline.forms:
        print(f"  {e.form_name}: bsl_sha256={e.bsl_sha256!r}, "
              f"elem_sha256={e.elem_sha256!r}")

    # Эмулируем изменение кода формы (bsl_sha256 изменится).
    form_bsl = (
        config_root / "Catalog" / "Товары" / "Forms" / "ФормаЭлемента" / "Form.obj.bsl"
    )
    form_bsl.write_text("// ИЗМЕНЁННЫЙ код формы", encoding="utf-8")
    current_after_bsl = scan_forms(config_root, mode="config")
    report_bsl = check_drift(baseline_path, current_after_bsl)
    print("\n[drift] после изменения кода:")
    print(f"  modified         = {report_bsl.modified}")
    print(f"  structure_modified = {report_bsl.structure_modified}")
    print(f"  has_drift        = {report_bsl.has_drift}")

    # Восстанавливаем код и эмулируем изменение разметки (elem_sha256 изменится).
    form_bsl.write_text("// исходный код", encoding="utf-8")
    form_elem = (
        config_root / "Catalog" / "Товары" / "Forms" / "ФормаЭлемента" / "Form.elem.json"
    )
    elem_changed = [
        {"name": "НоваяКнопка", "type": "Button", "path": "НоваяКнопка",
         "parent": None, "parent_path": None, "page": None,
         "source": None, "data_path": None, "handler": "НоваяКнопкаНажатие"}
    ]
    form_elem.write_text(
        json.dumps(elem_changed, ensure_ascii=False), encoding="utf-8"
    )
    current_after_elem = scan_forms(config_root, mode="config")
    report_elem = check_drift(baseline_path, current_after_elem)
    print("\n[drift] после изменения разметки (код не тронут):")
    print(f"  modified           = {report_elem.modified}")
    print(f"  structure_modified = {report_elem.structure_modified}")
    print(f"  has_drift          = {report_elem.has_drift}")


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

        # 4) external-режим: опись форм внешних обработок и отчётов (issue #32)
        external_root = base / "External"
        make_demo_external(external_root)
        ext_index = scan_forms(external_root, mode="external")
        print("\nВнешние формы (mode=external):", ext_index.total)
        for e in ext_index.forms:
            print(f"  - {e.object_type} / {e.object_name} / {e.form_name}"
                  f" [{e.container_name}] → {e.bsl_path.name}")

        # 5) drift-детекция: bsl_sha256 (issue #38) + elem_sha256 (issue #40)
        config_root = base / "config_forms"
        make_demo_config_forms(config_root)
        demo_drift(config_root)


if __name__ == "__main__":
    main()
