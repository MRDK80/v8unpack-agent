# v8unpack-agent

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![v8unpack](https://img.shields.io/badge/upstream-v8unpack-orange.svg)](https://github.com/saby-integration/v8unpack)

Надстройка над [v8unpack](https://github.com/saby-integration/v8unpack) для
агентных / LLM-пайплайнов по конфигурациям 1С.

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
  ├─► 0) scan_forms()               # опись всех форм по layout-у (вкл. формы без кода, #57)
  ├─► 1) unpack_all_forms()         # Form.bin → текстовый слой (BSL виден)
  │       └─► parse_elem_json()      # elem.json → form_elements_index (best-effort)
  │             ├─► object_decoder    # header → Properties, TabularSections (#84)
  │             ├─► catalog_resolver # data_path → ResolvedBinding (best-effort, #76)
  │             └─► form_classifier  # object / service / unknown (#98)
  ├─► 1') unpack_erf()              # внешний отчёт (.erf): текстовый слой
  │       └─► extract_skd_queries()  # СКД → skd_queries.json (best-effort)
  ├─► 2) update_forms_index()       # JSON-карта актуальности
  ├─► 3) check_drift()              # сравнение выгрузки с forms_scan_index
  └─► 4) rag.rebuild()              # code_context() видит формы + структуру + СКД
```

- **Идемпотентность.** Повторный прогон не перекладывает формы без изменений.
- **Отказоустойчивость.** `extraction_ok=False` по одной форме не роняет пайплайн.
- **Best-effort обогащение.** `parse_elem_json`, `extract_skd_queries`, `object_decoder` и `catalog_resolver` некритичны.
- **Привязка к данным.** `parse_elem_json` заполняет `data_path` несколькими
  подтверждёнными механизмами: обычные формы — через `prop`; управляемые —
  через UUID и консервативный структурный fallback; legacy
  `ФормаСписка` / `ФормаВыбора` с пустым `tree` — через привязки
  `TabularField` и UUID-карту реквизитов владельца. Элементы без
  подтверждённой привязки не угадываются.
- **Класс формы.** Мастера, помощники и диалоги привязывают поля к временным
  реквизитам формы, а не к объекту метаданных. `form_classifier` отделяет их
  от объектных форм, чтобы агрегированная метрика покрытия не была занижена
  архитектурным паттерном платформы (issue #98).
- **Неопределённость не маскируется.** Если разметку формы извлечь не удалось,
  класс остаётся `unknown` — форма исключается из знаменателя метрики, но не
  объявляется сервисной «по умолчанию».
- **Полнота описи.** `scan_forms` учитывает и управляемые формы без кода модуля
  (без `.obj.bsl`) — они попадают в индекс через `*.elem.json` (issue #57).
- **Прозрачность для агента.** Со стороны индексации это просто ещё один источник текстов.

## Публичная поверхность

| Модуль | Что даёт |
|---|---|
| `scan_forms` | `scan_forms()` + `FormEntry` + `FormScanIndex` — опись всех форм по layout-у выгрузки, включая формы без кода (`elem_json_path`, #57). Нулевой шаг пайплайна. → [подробнее](docs/scan_forms.md) |
| `drift_checker` | `check_drift()` + `DriftReport` — added / removed / modified (hash-based) / structure_modified (elem hash) / stale_extractions. → [подробнее](docs/drift_checker.md) |
| `form_router` | `FormRouter` — маршрутизация LLM-запроса к форме по имени объекта/формы. → [подробнее](docs/form_router.md) |
| `form_paths` | Фабрика путей по конвенции: `form_paths()`, `item_modules()`, `all_module_paths()`. Чистая арифметика путей. |
| `form_artifact` | `FormArtifact` — результат распаковки одной формы с явным флагом полноты. |
| `forms_index` | `FormsIndex` / `FormsIndexEntry` + `is_form_stale()` — реестр актуальности. |
| `managed_forms` | `discover_elem_forms()` + `ElemFormEntry` — обнаружение форм по `*.elem.json`. → [подробнее](docs/managed_forms_structure.md) |
| `pipeline` | `discover_form_bins()`, `unpack_all_forms()`, `update_forms_index()`, `unpack_erf()`, `ErfUnpacker`. |
| `skd_extractor` | `extract_skd_queries()` + `extract_all_skd_queries()` — СКД из `.erf`. → [подробнее](docs/skd_extractor.md) |
| `elem_parser` | `parse_elem_json()` + `ElemIndexResult` — структура формы из `elem.json`; `data_path` обычных форм через `prop`, управляемых — через UUID и структурный fallback, legacy `ФормаСписка` / `ФормаВыбора` — через подтверждённые UUID-привязки `TabularField` (#103). → [подробнее](docs/elem_parser.md) |
| `form_summary` | `build_form_summary(form_dir)` + `to_normalized_json()` — детерминированная семантическая выжимка любой elem-формы (обычной и управляемой): attributes / commands / elements / events / relations поверх `parse_elem_json`. → [подробнее](docs/form_summary.md) |
| `catalog_resolver` | `resolve_data_path()` + `ResolvedBinding` + `object_json_path()` — best-effort обогащение подтверждённого `data_path` через JSON объекта (#76). Извлечение путей реализовано в #85, чтение имени, UUID и типа реквизита — в #84. Приведение `Ref#uuid` к имени объекта метаданных — #88. → [подробнее](docs/catalog_resolver.md) |
| `object_decoder` | `decode_object_attributes()` + `DecodeResult` — реквизиты объекта из raw `header`: имя, UUID, тип, табличные части. Питает карту реквизитов `elem_parser` и `catalog_resolver` (#84). → [подробнее](docs/object_decoder.md) |
| `coverage_metric` | `calc_data_path_coverage()` + `CoverageReport` — метрика покрытия `data_path` только по элементам данных (`Field`, `Table`, `CheckBox`...), без служебных (`Label`, `Group`, `Panel`...). Поле `form_class` и параметр `form_name` (#98). Константы `DATA_ELEMENT_TYPES`, `SERVICE_ELEMENT_TYPES`, `PLATFORM_STANDARD_ATTRIBUTES` (#90). |
| `form_classifier` | `classify_form()` + `FormClass` + `classify_empty_tree_form()` — разделение форм на объектные и сервисные по имени и структуре привязок; диагностика форм с пустым `tree` (#98). |

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


# 0) опись форм (включает формы без кода: elem_json_path заполнен, #57)
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

Полные примеры:

- [`examples/basic_usage.py`](examples/basic_usage.py) — распаковка, реестр форм и drift-контроль.
- [`examples/form_bindings.py`](examples/form_bindings.py) — декодирование `data_path` для обычной формы через `prop` и для управляемой формы через UUID реквизита (issue #85).
- [`examples/coverage_metric.py`](examples/coverage_metric.py) — расчёт покрытия `data_path` только по элементам данных, классификация объектных и сервисных форм и JSON-отчёт (`CoverageReport`, issues #90, #98).
- [`examples/extract_skd_queries.py`](examples/extract_skd_queries.py) — извлечение запросов СКД из распакованного внешнего отчёта.
- [`examples/legacy_list_form_bindings.py`](examples/legacy_list_form_bindings.py) — извлечение подтверждённых привязок колонок legacy `ФормаСписка` / `ФормаВыбора` через fallback `TabularField` (#103).

## Классификация форм

Формы 1С делятся на объектные и сервисные. Объектная форма привязывает поля
к реквизитам объекта метаданных через путь `Объект.Реквизит`. Сервисная форма
(мастер, помощник, диалог, информационная) использует временные реквизиты
самой формы — привязок `Объект.*` у неё нет по архитектуре, а не по ошибке.

```python
from v8unpack_agent.form_classifier import classify_form

form_class = classify_form(
    form_name="ПомощникПодключенияЭДО",
    elements=result.elements,
)
# FormClass.SERVICE
```

Класс формы автоматически попадает в `CoverageReport`:

```python
from v8unpack_agent.coverage_metric import calc_data_path_coverage

report = calc_data_path_coverage(result.elements, form_name="ФормаЭлемента")
print(report.form_class)   # "object"
print(report.coverage_pct) # 78.6
```

| Класс | Критерий | Роль в метрике |
|---|---|---|
| `object` | Есть `data_path`, начинающийся с `Объект.` | Входит в агрегированное покрытие |
| `service` | Имя из `SERVICE_FORM_NAME_PATTERNS` либо ни одной привязки `Объект.*` | Считается отдельно |
| `unknown` | Элементы не извлечены из `.elem.json` | Исключена из знаменателя |

Критерии объединяются по OR: достаточно одного признака, чтобы форма
считалась сервисной. Широкие префиксы вроде `форма` в список паттернов
намеренно не входят — под них попадают стандартные объектные формы
платформы (`ФормаЭлемента`, `ФормаДокумента`, `ФормаЗаписи`).

### Формы без извлечённой разметки

Пустой `tree` в `.elem.json` не означает сервисную форму. Для таких случаев
используйте `classify_empty_tree_form()` — она возвращает пару
«класс, причина»:

```python
from v8unpack_agent.form_classifier import classify_empty_tree_form

form_class, reason = classify_empty_tree_form("ФормаЗаписи")
# (FormClass.UNKNOWN, "platform_object_name_unparsed")
```

| Причина | Класс | Смысл |
|---|---|---|
| `by_service_pattern` | `service` | Совпадение с проверенным списком — единственный случай, когда класс присваивается без разметки |
| `platform_object_name_unparsed` | `unknown` | Стандартное имя объектной формы платформы; сигнал о пробеле в `elem_parser` |
| `empty_tree_name_hint` | `unknown` | Имя похоже на сервисное, подтверждения нет |
| `unparsed_empty_tree` | `unknown` | Имя не распознано, разметки нет |
| `no_name` | `unknown` | Имя формы не передано |

### Верификация на реальном корпусе

Результат на локальной конфигурации УТ 10.3 (2231 директория форм):

| Категория | Кол-во |
|---|---|
| `object` | 164 |
| `service` | 1793 |
| `unknown` | 80 |
| Стандартные автогенерируемые | 194 |
| Ошибок | 0 |

Агрегированное покрытие `data_path` считается только по 1957 формам с реально
распарсенной разметкой (164 + 1793). Оставшиеся 80 — обычные (неуправляемые)
формы, разметка которых хранится в бинарном виде и не читается текущим
парсером. Они честно помечены `unknown` и не искажают метрику.

> Эта таблица зафиксирована до реализации fallback #103. Отдельный полный
> прогон `parse_elem_json()` после #103: 2216 форм, 2139 успешно
> проиндексированы, 77 остаются неразобранными; дальнейшая классификация
> вынесена в #105.

## Документация

| Тема | Файл |
|---|---|
| Сканер форм: layout, FormEntry, elem-only, CLI, external-режим | [docs/scan_forms.md](docs/scan_forms.md) |
| Контроль дрейфа: DriftReport, алгоритм, сценарии | [docs/drift_checker.md](docs/drift_checker.md) |
| Маршрутизация агента: FormRouter, приоритеты | [docs/form_router.md](docs/form_router.md) |
| Внешние отчёты (.erf), СКД, Template.bin | [docs/skd_extractor.md](docs/skd_extractor.md) |
| elem.json и form_elements_index | [docs/elem_parser.md](docs/elem_parser.md) |
| Discovery форм по `*.elem.json` | [docs/managed_forms_structure.md](docs/managed_forms_structure.md) |
| Структура распакованных внешних обработок | [docs/external_forms_structure.md](docs/external_forms_structure.md) |
| Семантическая выжимка elem-формы поверх parse_elem_json | [docs/form_summary.md](docs/form_summary.md) |
| `catalog_resolver`: резолюция `data_path` через описание объекта | [docs/catalog_resolver.md](docs/catalog_resolver.md) |
| Реквизиты объекта из raw `header`: типы, UUID, табличные части | [docs/object_decoder.md](docs/object_decoder.md) |
| Классификация форм: объектные vs. сервисные, пустой `tree` | [docs/form_classifier.md](docs/form_classifier.md) |

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
pip install -e "[test]"
pytest
```

Набор тестов полностью синтетический: проверка идёт на временных файловых
деревьях с внедрённым распаковщиком-заглушкой — реальный контейнер 1С не
требуется.

Изменения, влияющие на классификацию форм и метрики покрытия,
дополнительно проверяются на реальной выгрузке конфигурации. Такие
прогоны выполняются локально: скрипты verify_* не входят в
репозиторий, поскольку требуют production-данных. Текстовый вывод
верификации прикладывается к pull request.

## Связанное

- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — нижележащий распаковщик контейнеров (Python, MIT)
- [PR#29 — fix: add ExternalReport (.erf) support](https://github.com/saby-integration/v8unpack/pull/29) — принят
- [Обычные формы 1С в агентном пайплайне: пошаговая распаковка](https://infostart.ru/1c/articles/2721726/)
- [СКД и дерево элементов обычной формы 1С: два некритичных шага в агентном пайплайне](https://infostart.ru/1c/articles/2726561/)
- [Реестр форм 1С для агента: scan_forms и первый агрегат поверх распаковки](https://infostart.ru/1c/articles/2735755/)
- [Управляемые формы 1С после распаковки: единый конвейер поверх *.elem.json](https://infostart.ru/1c/articles/2746487/)

## Лицензия

Дистрибутируется под лицензией **[MIT](LICENSE)**. Используйте свободно —
с сохранением текста лицензии и указанием авторства.
