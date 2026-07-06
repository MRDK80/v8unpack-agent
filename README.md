# v8unpack-agent

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![v8unpack](https://img.shields.io/badge/upstream-v8unpack-orange.svg)](https://github.com/saby-integration/v8unpack)]

Надстройка над [v8unpack](https://github.com/saby-integration/v8unpack) для
агентных / LLM-пайплайнов по конфигурациям 1С.

Реализует доработки из статей
**[Обычные формы 1С в агентном пайплайне: пошаговая распаковка](https://infostart.ru/1c/articles/2721726/)**
и **[СКД и дерево элементов обычной формы 1С: два некритичных шага в агентном пайплайне](https://infostart.ru/1c/articles/2726561/)**.

**Этот пакет сам не разбирает бинарные формы 1С.** Реальную распаковку
выполняет [v8unpack](https://github.com/saby-integration/v8unpack) (Python, MIT).
Здесь — обвязка: *куда* класть результат, *насколько* он полон и *не устарел* ли он.

## Кто что решает

1. **`v8unpack` (upstream)** — превращает контейнеры `cf/cfe/epf/erf` в
   человекочитаемое дерево файлов и выносит **код** обычных форм в отдельные
   файлы.
2. **`v8unpack-agent` (этот пакет)** — не трогает бинарное содержимое. Поверх
   распаковщика выстраивает пайплайн под кейс агента.

## Пайплайн

```
index_cf(<путь_к_выгрузке>)
  ├─► 0) scan_forms()               # опись всех форм по layout-у выгрузки
  ├─► 1) unpack_all_forms()         # Form.bin → текстовый слой (BSL виден)
  │       └─► parse_elem_json()      # elem.json → form_elements_index (best-effort)
  ├─► 1') unpack_erf()              # внешний отчёт (.erf): текстовый слой
  │       └─► extract_skd_queries()  # СКД → skd_queries.json (best-effort)
  ├─► 2) update_forms_index()       # JSON-карта актуальности
  ├─► 3) check_drift()              # сравнение выгрузки с forms_scan_index
  └─► 4) rag.rebuild()              # code_context() видит формы + структуру + СКД
```

- **Идемпотентность.** Повторный прогон не перекладывает формы без изменений.
- **Отказоустойчивость.** `extraction_ok=False` по одной форме не роняет пайплайн.
- **Best-effort обогащение.** `parse_elem_json` и `extract_skd_queries` некритичны.
- **Прозрачность для агента.** Со стороны индексации это просто ещё один источник текстов.

## Публичная поверхность

| Модуль | Что даёт |
|---|---|
| `scan_forms` | `scan_forms()` + `FormEntry` + `FormScanIndex` — опись всех форм по layout-у выгрузки. Нулевой шаг пайплайна. → [подробнее](docs/scan_forms.md) |
| `drift_checker` | `check_drift()` + `DriftReport` — added / removed / modified (hash-based) / structure_modified (elem hash) / stale_extractions. → [подробнее](docs/drift_checker.md) |
| `form_router` | `FormRouter` — маршрутизация LLM-запроса к форме по имени объекта/формы. → [подробнее](docs/form_router.md) |
| `form_paths` | Фабрика путей по конвенции: `form_paths()`, `item_modules()`, `all_module_paths()`. Чистая арифметика путей. |
| `form_artifact` | `FormArtifact` — результат распаковки одной формы с явным флагом полноты. |
| `forms_index` | `FormsIndex` / `FormsIndexEntry` + `is_form_stale()` — реестр актуальности. |
| `pipeline` | `discover_form_bins()`, `unpack_all_forms()`, `update_forms_index()`, `unpack_erf()`, `ErfUnpacker`. |
| `skd_extractor` | `extract_skd_queries()` + `extract_all_skd_queries()` — СКД из `.erf`. → [подробнее](docs/skd_extractor.md) |
| `elem_parser` | `parse_elem_json()` + `ElemIndexResult` — структура формы из `elem.json`. → [подробнее](docs/elem_parser.md) |

## Быстрый старт

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import (
    FormArtifact, form_paths,
    unpack_all_forms, update_forms_index,
    is_form_stale, check_drift,
)
from v8unpack_agent.scan_forms import scan_forms

dump_root     = Path("unpacked_cf/")
unpacked_root = Path("text_layer/")


def unpack_one(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
    target = root / "Form" / form_name
    target.mkdir(parents=True, exist_ok=True)
    v8unpack.extract(str(bin_path), str(target))
    paths = form_paths(root, form_name)
    if paths["object_module"].exists():
        return FormArtifact.for_form(root, form_name)
    return FormArtifact.for_form(
        root, form_name,
        extraction_ok=False,
        extraction_warnings=["код формы не распакован"],
    )


# 0) опись форм
scan_index = scan_forms(dump_root, save_to=Path("forms_scan_index.json"))
print(f"Всего форм: {scan_index.total}")

# 3) контроль дрейфа
report = check_drift(dump_root, index_path=Path("forms_scan_index.json"))
if report.has_drift:
    print("Изменены (код):   ", report.modified)           # hash-based (#38)
    print("Изменены (разм.): ", report.structure_modified)  # elem hash  (#40)

# 1-2) распаковка и обновление индекса
artifacts = unpack_all_forms(dump_root, unpacked_root, unpack_one)
index = update_forms_index(dump_root, unpacked_root, artifacts)
index.save(Path("forms_index.json"))
```

Полный пример: [`examples/basic_usage.py`](examples/basic_usage.py).

## Документация

| Тема | Файл |
|---|---|
| Сканер форм: layout, FormEntry, CLI, external-режим | [docs/scan_forms.md](docs/scan_forms.md) |
| Контроль дрейфа: DriftReport, алгоритм, сценарии | [docs/drift_checker.md](docs/drift_checker.md) |
| Маршрутизация агента: FormRouter, приоритеты | [docs/form_router.md](docs/form_router.md) |
| Внешние отчёты (.erf), СКД, Template.bin | [docs/skd_extractor.md](docs/skd_extractor.md) |
| elem.json и form_elements_index | [docs/elem_parser.md](docs/elem_parser.md) |
| Структура распакованных внешних обработок | [docs/external_forms_structure.md](docs/external_forms_structure.md) |

## Установка

Пока не опубликовано в PyPI. Установка из репозитория:

```bash
pip install "v8unpack>=1.2.9"   # поддержка .erf включена начиная с этой версии
pip install git+https://github.com/MRDK80/v8unpack-agent.git
```

или из локального checkout:

```bash
pip install -e .
```

## Тесты

```bash
pip install -e ".[test]"
pytest
```

Набор тестов полностью синтетический: проверка идёт на временных файловых
деревьях с внедрённым распаковщиком-заглушкой — реальный контейнер 1С не
требуется.

## Связанное

- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — нижележащий распаковщик контейнеров (Python, MIT)
- [PR#29 — fix: add ExternalReport (.erf) support](https://github.com/saby-integration/v8unpack/pull/29) — принят
- [Обычные формы 1С в агентном пайплайне: пошаговая распаковка](https://infostart.ru/1c/articles/2721726/)
- [СКД и дерево элементов обычной формы 1С: два некритичных шага в агентном пайплайне](https://infostart.ru/1c/articles/2726561/)

## Лицензия

MIT
