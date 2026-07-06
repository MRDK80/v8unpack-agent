# v8unpack-agent

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![v8unpack](https://img.shields.io/badge/upstream-v8unpack-orange.svg)](https://github.com/saby-integration/v8unpack)]

Надстройка над [v8unpack](https://github.com/saby-integration/v8unpack) для
агентных / LLM-пайплайнов по конфигурациям 1С.

Реализует доработки из статей **[Обычные формы 1С в агентном пайплайне: пошаговая распаковка](https://infostart.ru/1c/articles/2721726/)**
и **[СКД и дерево элементов обычной формы 1С: два некритичных шага в агентном пайплайне](https://infostart.ru/1c/articles/2726561/)**

**Этот пакет сам не разбирает бинарные формы 1С.** Реальную распаковку
выполняет [v8unpack](https://github.com/saby-integration/v8unpack) (Python, MIT).
Здесь — обвязка: *куда* класть результат, *насколько* он полон и *не устарел* ли он.

## Кто что решает

1. **`v8unpack` (upstream)** — превращает контейнеры `cf/cfe/epf/erf` в
   человекочитаемое дерево файлов и выносит **код** обычных форм в
   отдельные файлы. Его собственное ограничение: разметка форм и часть свойств
   остаются нечитаемыми.
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

- **Идемпотентность.** Повторный прогон не перекладывает формы, у которых
  `bin_mtime == unpacked_mtime` — только новые/изменённые.
- **Отказоустойчивость.** Если по одной форме `extraction_ok=False` — пайплайн
  не падает, реестр честно помечает её как частичную.
- **Best-effort обогащение.** Шаги `parse_elem_json` и `extract_skd_queries`
  некритичные: их неудача не меняет `extraction_ok`, а лишь оставляет
  `elem_index_ok=False` / `skd_extracted=False` и дополняет предупреждения.
- **Прозрачность для агента.** Со стороны индексации это просто ещё один
  источник текстов.

## Сканер форм (scan_forms)

`scan_forms(cf_export_root)` обходит дерево выгрузки и строит `FormScanIndex` —
опись всех форм с их артефактами (`.obj.bsl` + `.json`) без какого-либо парсинга
содержимого. Это нулевой шаг пайплайна: сначала узнаём что есть, потом
распаковываем.

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms

root = Path("/path/to/cf_export")
index = scan_forms(root, save_to=Path("forms_scan_index.json"))

print(f"Найдено форм: {index.total}")
for entry in index.forms:
    print(entry.container_name, entry.object_name, entry.form_name)
```

### CLI

    python -m v8unpack_agent.scan_forms <root> [--mode {config,external}] [--save]

- `--mode config` (по умолчанию) — структура конфигурации;
- `--mode external` — распакованные внешние обработки и отчёты (issues #25, #32);
- `--save` — сохранить `forms_scan_index.json` в `<root>`.

### Layout выгрузки v8unpack

Сканер поддерживает два layout-а:

**4-уровневый** (все типы кроме CommonForm):

```
<root>/
  <ObjectType>/           # Catalog, Document, DataProcessor, …
    <ObjectName>/         # Склады, Контрагенты, …
      <ContainerName>/    # CatalogForm, DocumentForm, Form, ReportForm, …
        <FormName>/       # ФормаЭлемента, ФормаСписка, …
          <ContainerName>.obj.bsl
          <ContainerName>.json
```

**3-уровневый** (CommonForm — общие формы конфигурации):

```
<root>/
  CommonForm/
    <FormName>/           # НастройкаПрограммы, ВыборСертификата, …
      CommonForm.obj.bsl
      CommonForm.json
```

### Layout внешних обработок и отчётов (mode="external")

Распакованные внешние обработки (`.epf`) и отчёты (`.erf`) имеют структуру,
отличную от конфигурации (issues #25, #32, подтверждено на реальных данных).

**Внешняя обработка** — контейнер форм `Form/`:
```
External/<имя обработки>/
    ExternalDataProcessor.obj.bsl   # модуль объекта (не форма)
    Form/
        <ИмяФормы>/
            Form.obj.bsl            # bsl формы (v8unpack 1.2.11)
            Form.json
            Form.elem               # структура формы (элементы)
            Form.id
```

**Внешний отчёт** — контейнер форм `ReportForm/`:
```
External/<имя отчёта>/
    ReportForm/
        <ИмяФормы>/
            ReportForm.obj.bsl      # bsl формы (v8unpack 1.2.11)
            Form.json
            Form.elem
            Form.id
```

Ключевые особенности external-режима:

- **bsl-файл формы называется `<Container>.obj.bsl`** (`Form.obj.bsl` для
  обработки, `ReportForm.obj.bsl` для отчёта) на v8unpack 1.2.11. Для обратной
  совместимости со старыми выгрузками поддержан вариант без суффикса —
  `<Container>.obj` (`Form.obj` / `ReportForm.obj`); берётся первый существующий,
  приоритет у `.bsl` (issue #32).
- **Верхний уровень** — имя конкретной обработки/отчёта, а не `object_type`.
- **Контейнер форм** — `Form/` (обработка) либо `ReportForm/` (отчёт).

Для этой структуры используется отдельный режим:

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms

index = scan_forms(Path("/path/to/External"), mode="external",
                   save_to=Path("forms_scan_index.json"))
```

Определение `object_type` в external-режиме (issue #32):

- контейнер `ReportForm/` ⇒ `object_type="ExternalReport"` (по контейнеру,
  приоритет над модулем объекта);
- контейнер `Form/` ⇒ по имени модуля объекта
  (`ExternalDataProcessor.obj.bsl` / `ExternalReport.obj.bsl`), при отсутствии —
  fallback `ExternalDataProcessor`.

У формы `object_name` = имя обработки/отчёта, `container_name` = `"Form"` либо
`"ReportForm"`. Оба типа (`ExternalDataProcessor`, `ExternalReport`) не
пересекаются с типами конфигурации. Подробнее — [Структура распакованных внешних
обработок](docs/external_forms_structure.md).

### Семантика контейнеров

| Контейнер | Типы объектов |
|---|---|
| `Form` | `DataProcessor` (внутри `.cf`) и `ExternalDataProcessor` (`.epf`) — различать по `object_type` |
| `ReportForm` | `Report` (`.cf`) и `ExternalReport` (`.erf`) — различать по `object_type` |
| `CatalogForm`, `DocumentForm`, `InformationRegisterForm`, … | однозначно определяются именем контейнера |
| `CommonForm` | общие формы, без уровня `ObjectName` |

Паттерн обхода: `endswith("Form")` — нет хардкода конкретных имён контейнеров.

### FormEntry и FormScanIndex

`FormEntry` — dataclass, результат обхода одной формы:

| Поле | Тип | Значение |
|---|---|---|
| `object_type` | string | Тип объекта метаданных: `Catalog`, `Document`, `DataProcessor`, … Для external — `ExternalDataProcessor` / `ExternalReport`. |
| `object_name` | string | Имя объекта: `Склады`, `Контрагенты`, … (для `CommonForm` совпадает с `container_name`; для external — имя обработки/отчёта) |
| `container_name` | string | Имя контейнера форм: `CatalogForm`, `Form`, `ReportForm`, `CommonForm`, … |
| `form_name` | string | Имя формы: `ФормаЭлемента`, `ФормаСписка`, … |
| `form_path` | string | Путь к директории формы относительно корня выгрузки |
| `bsl_path` | string | Путь к bsl-файлу формы (`<Container>.obj.bsl` или legacy `<Container>.obj`) относительно корня выгрузки |
| `json_path` | string | Путь к `.json` относительно корня выгрузки |
| `bsl_mtime` | float | `st_mtime` bsl-файла на момент сканирования. Legacy-поле; используется как fallback в `drift_checker` для старых индексов без `bsl_sha256`. `0.0` — неизвестно. |
| `bsl_sha256` | string \| null | SHA-256 hex-дайджест содержимого bsl-файла на момент сканирования. Основной критерий изменения кода формы в `check_drift()` (issue #38). `null` в старых индексах без hash-поля — используется legacy fallback через `bsl_mtime`. |
| `elem_sha256` | string \| null | SHA-256 hex-дайджест нормализованного дерева элементов формы (issue #40). Хэшируются только структурно значимые поля: `name`, `type`, `path`, `parent`, `parent_path`, `page`, `source`, `data_path`, `handler`. Косметика (координаты, цвета, шрифты, GUID) исключена. `null` — `*.elem.json` не найден, не разобран или список элементов пуст. Независимый сигнал `structure_modified` в `check_drift()`. |
| `warnings` | array | Предупреждения (обычно пусто) |
| `form_elem_path` | string \| null | Путь к `Form.elem` (структура формы внешнего объекта, mode="external"). `null` для форм конфигурации или если файла нет. |

`FormScanIndex` содержит список `forms`, счётчик `total`, метку `scanned_at` и
список `scan_warnings` (пропущенные формы без bsl-файла).

Для загрузки сохранённого индекса используй `FormScanIndex.load(path)`:

```python
from v8unpack_agent.scan_forms import FormScanIndex

index = FormScanIndex.load(Path("forms_scan_index.json"))
# Старые индексы без bsl_sha256: поле получает None (backward-compat).
# Старые индексы без bsl_mtime: поле получает 0.0 (backward-compat).
# Старые индексы без elem_sha256: поле получает None (backward-compat).
```

### Поведение при ошибках

- Форма без bsl-файла → `skipped (no <Container>.obj.bsl / <Container>.obj): <path>` в `scan_warnings`, в индекс не попадает.
- Ошибка в одной форме не останавливает обход (best-effort).

## Контроль дрейфа (drift_checker)

`check_drift(cf_export_root, index_path)` сравнивает текущее состояние
выгрузки на диске с ранее сохранённым `forms_scan_index.json` и возвращает
`DriftReport` — отчёт о расхождениях.

```python
from pathlib import Path
from v8unpack_agent import check_drift

report = check_drift(
    cf_export_root=Path("/path/to/cf_export"),
    index_path=Path("forms_scan_index.json"),
    save_to=Path("drift_report.json"),   # опционально
)

if report.has_drift:
    print("Добавлены:",         report.added)
    print("Удалены:  ",         report.removed)
    print("Изменены (код): ",   report.modified)
    print("Изменены (разм.):",  report.structure_modified)
    print("Stale BSL:",         report.stale_extractions)
else:
    print("Дрейфа нет, индекс актуален")
```

### DriftReport

| Поле | Тип | Значение |
|---|---|---|
| `added` | list[str] | Ключи форм, появившихся на диске после последнего сканирования |
| `removed` | list[str] | Ключи форм, исчезнувших с диска (были в индексе) |
| `modified` | list[str] | Ключи форм с изменившимся содержимым BSL-файла (код формы). **Алгоритм:** если в baseline-индексе есть `bsl_sha256` — сравнивается hash текущего файла с сохранённым (issue #38); изменение только `mtime` при неизменном содержимом **не** помечает форму как modified. Если `bsl_sha256` отсутствует (старый индекс) — legacy fallback: сравнивается `bsl_mtime` с допуском 1 сек. |
| `structure_modified` | list[str] | Ключи форм с изменившимся деревом элементов (разметка формы), при неизменном BSL (issue #40). **Алгоритм:** если в baseline-индексе есть `elem_sha256` — пересчитывается хэш нормализованного дерева элементов текущей формы и сравнивается с сохранённым. Если `elem_sha256` отсутствует (старый индекс) — сигнал не порождается. Независимый сигнал от `modified`. |
| `stale_extractions` | list[str] | Формы из индекса, чей `bsl_path` не существует на диске |
| `has_drift` | bool | `True` если хотя бы одно из полей непусто |
| `checked_at` | str | ISO 8601 метка времени проверки |

**Ключ формы** имеет вид `"ObjectType/ObjectName/ContainerName/FormName"`.
Для CommonForm: `"CommonForm//CommonForm/ФормаИмя"`.

### Типичные сценарии детекции

| Действие | `modified` | `structure_modified` |
|---|---|---|
| Правка кода формы (BSL), разметка не тронута | ✓ | — |
| Добавление/удаление элемента на форме, BSL не тронут | — | ✓ |
| Одновременная правка кода и разметки | ✓ | ✓ |
| Косметика формы (координаты, цвета) без смысловых изменений | — | — |

### Поведение при отсутствии индекса

Если `index_path` не найден — `added` содержит все формы на диске,
`has_drift=True`. Исключение не бросается — это штатная ситуация первого
запуска.

### Сохранение и загрузка отчёта

```python
from v8unpack_agent import DriftReport

report = DriftReport.load_from(Path("drift_report.json"))
print(report.checked_at, report.has_drift)
```

## Внешние отчёты (.erf)

`.erf`-файлы распаковываются в два шага:

```
my_report.erf
  └─► unpack_erf() / v8unpack.extract() → текстовый слой
       └─► extract_skd_queries()         → skd_queries.json
```

```python
from pathlib import Path
from v8unpack_agent import unpack_erf, extract_skd_queries

result = unpack_erf(Path("my_report.erf"), Path("text_layer/my_report"))
skd    = extract_skd_queries(Path("text_layer/my_report"))

print(skd.skd_extracted)   # True
for ds in skd.datasets:
    print(ds["name"], "|", ds["query"][:60])
```

Если `skd_extracted=False` — агент видит только BSL модуля отчёта. Это не
ошибка пайплайна, а сигнал о неполноте контекста (отчёт без СКД или
нераспознанный формат `Template.bin`).

### Пакетный режим СКД

Если под корнем несколько отчётов — `extract_skd_queries()` берёт только
первый `Template.bin`. Для обхода всех используй пакетный вариант:

```python
from pathlib import Path
from v8unpack_agent import extract_all_skd_queries, SkdBatchResult

batch: SkdBatchResult = extract_all_skd_queries(Path("text_layer/Report"))

print(batch.skd_extracted)          # True если хотя бы один отчёт извлёкся
for result in batch.results:
    if result.skd_extracted:
        print(result.datasets[0]["name"], "|", result.datasets[0]["query"][:60])
print(batch.warnings)               # предупреждения по неудачным отчётам
```

Ошибка одного отчёта не прерывает обработку остальных.

## elem.json и form_elements_index

После распаковки обычной формы агент видит не только `Form.obj.bsl`, но и `*.elem.json`.
`Form.obj.bsl` содержит обработчики, а `elem.json` — структуру формы: группы, панели,
кнопки, поля, привязки и страницы.

```python
from pathlib import Path
from v8unpack_agent import parse_elem_json

result = parse_elem_json(Path("unpacked/Form/ФормаЭлемента"))

if result.elem_index_ok:
    print(result.elements)
else:
    print(result.warnings)
```

Если `elem_index_ok=False`, основной текстовый слой формы остаётся доступен.
Это не ошибка распаковки, а сигнал, что структурный контекст формы неполный.

## Публичная поверхность

| Модуль | Что даёт |
|---|---|
| `scan_forms` | `scan_forms()` + `FormEntry` + `FormScanIndex` — опись всех форм по layout-у выгрузки (все `*Form`-контейнеры + `CommonForm`; external — `Form`/`ReportForm`), best-effort, JSON-экспорт. Нулевой шаг пайплайна: файловая система, без парсинга BSL. |
| `drift_checker` | `check_drift()` + `DriftReport` — сравнение текущей выгрузки с сохранённым `FormScanIndex`: added / removed / **modified** (hash-based, issue #38) / stale_extractions / **structure_modified** (elem hash, issue #40), JSON-сохранение отчёта. |
| `form_paths` | Фабрика путей по конвенции: `form_paths()`, `item_modules()` (вложенные панели из `Items/`), `all_module_paths()`. Чистая арифметика путей — файлы не читаются. |
| `form_artifact` | `FormArtifact` (`name`, `paths`, `extraction_ok`, `extraction_warnings`, `skd_extracted`, `elem_index_ok`) — результат распаковки одной формы с явным флагом полноты, без тихого провала. |
| `forms_index` | `FormsIndex` / `FormsIndexEntry` + `is_form_stale()` — реестр актуальности по `bin_mtime` vs `unpacked_mtime`. |
| `pipeline` | `discover_form_bins()`, `unpack_all_forms()`, `update_forms_index()`, `unpack_erf()`, `ErfUnpacker` — распаковка форм и `.erf` как pre-step индексации. |
| `skd_extractor` | `extract_skd_queries()` + `SkdResult` — покейсовый режим: один отчёт → `skd_queries.json`. `extract_all_skd_queries()` + `SkdBatchResult` — пакетный: обходит все `Template.bin` под корнем, ошибка одного отчёта не прерывает остальные. |
| `elem_parser` | `parse_elem_json()` + `ElemIndexResult` — структура формы из `elem.json` в `form_elements_index.json` (best-effort, не влияет на `extraction_ok`). |

### Конвенция путей (формы)

```
<unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
<unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
<unpacked_root>/Form/<имя>/Form.json             # метаданные формы
<unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
```

### Конвенция путей (внешние отчёты)

```
<unpacked_root>/
  ExternalDataProcessor.obj.bsl                  # BSL модуль отчёта
  Template/
    ОсновнаяСхемаКомпоновкиДанных/
      Template.bin                               # v8-контейнер с XML СКД
<unpacked_root>.parent/
  skd_queries.json                               # извлечённые запросы СКД
```

`Template.bin` — v8-контейнер с переменным заголовком (8 или 24 байта в
зависимости от версии платформы) + UTF-8 BOM (метка порядка байт) + XML схемы компоновки данных.
`extract_skd_queries()` определяет начало XML динамически — по BOM, а при его
отсутствии по `<?xml`.

### Запись `forms_index`

| Поле | Тип | Значение |
|---|---|---|
| `bin_path` | string | Путь к исходному `Form.bin` относительно корня выгрузки |
| `unpacked_root` | string | Путь к директории с распакованными текстами |
| `bin_mtime` | float | Unix-время изменения `Form.bin` в момент распаковки |
| `unpacked_mtime` | float | Unix-время последней распаковки; если `bin_mtime > unpacked_mtime` — форма устарела |
| `extraction_ok` | bool | `true` — полная распаковка; `false` — частичная |
| `warnings` | array | Диагностические сообщения при частичной распаковке; пустой массив при `extraction_ok: true` |

`forms_index` — **не** источник истины (источник — `Form.bin` в выгрузке), а
**карта актуальности**. В неё кладётся только маршрутизация и метки времени:
никакого содержимого `Form.bin`, строк подключения, имён баз/хостов — реестр
остаётся обезличенным и коммитится в репозиторий вместе с выгрузкой.

## Маршрутизация агента через FormScanIndex

`FormRouter` — инструмент для LLM-агента: LLM извлекает имя объекта
или формы из запроса пользователя и передаёт его в `route()`.
Роутер не делает LLM-вызовов — только строковое сопоставление.

```python
from pathlib import Path
from v8unpack_agent.form_router import FormRouter

router = FormRouter(index_path=Path("forms_scan_index.json"))

# LLM передаёт извлечённую сущность:
result = router.route("Банки")
# result.matched  — список FormEntry с путями к .bsl и .json
# result.confidence — 0.0–1.0
# result.warnings   — при нулевом результате

for entry in result.matched:
    print(entry.object_type, entry.object_name, entry.form_name)
    print(entry.bsl_path)   # путь к исходнику формы
```

### Приоритет совпадений

| Уровень | Поле | Тип | conf |
|---|---|---|---|
| 1 | `form_name` | точное | 1.0 |
| 2 | `object_name` | точное, case-insensitive | 0.9 |
| 3 | `object_type` | частичное, case-insensitive | 0.4 |

Сравнение на уровнях 2–3 регистронезависимо: LLM может вернуть
`"банки"`, `"Банки"` или `"БАНКИ"` — все три найдут `Catalog/Банки`.

### Внешние обработки и отчёты в маршрутизации

`FormRouter` работает поверх единого `FormScanIndex`, поэтому формы внешних
обработок и отчётов (`mode="external"`) маршрутизируются тем же `route()` без
отдельного API. Такие формы имеют `object_type="ExternalDataProcessor"` либо
`"ExternalReport"`, `object_name` = имя обработки/отчёта, `container_name` =
`"Form"` либо `"ReportForm"` (см. раздел про `mode="external"`), и находятся по
тем же трём уровням совпадения:

```python
# индекс собран из внешних обработок/отчётов: scan_forms(..., mode="external")
router = FormRouter(index_path=Path("forms_scan_index.json"))

result = router.route("ExternalReport")   # по object_type → conf 0.4
result = router.route("ЗагрузкаЦен")      # по object_name  → conf 0.9
```

`object_type` external-объектов (`ExternalDataProcessor` / `ExternalReport`) не
пересекается с типами конфигурации, поэтому формы обработок/отчётов и одноимённые
формы конфигурации не коллидируют в одном индексе. Смешанный индекс
(конфигурация + External) поддерживается: при неоднозначности приоритет отдаётся
`form_name` (1.0) над `object_name` (0.9).

### Инкрементальное обновление индекса

```python
router.reindex([updated_entry])   # обновляет без полного пересканирования
```

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

## Быстрый старт

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import (
    FormArtifact,
    form_paths,
    unpack_all_forms,
    update_forms_index,
    is_form_stale,
    check_drift,
)

dump_root = Path("unpacked_cf/")     # дерево, полученное от v8unpack
unpacked_root = Path("text_layer/")  # куда складываем текстовый слой форм


def unpack_one(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
    """Распаковать одну форму через v8unpack и вернуть артефакт."""
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


# 0) опись всех форм по layout-у выгрузки
from v8unpack_agent.scan_forms import scan_forms
scan_index = scan_forms(dump_root, save_to=Path("forms_scan_index.json"))
print(f"Всего форм в конфигурации: {scan_index.total}")

# 3) контроль дрейфа (после повторного получения выгрузки)
report = check_drift(
    cf_export_root=dump_root,
    index_path=Path("forms_scan_index.json"),
)
if report.has_drift:
    print("Добавлены:",        report.added)
    print("Удалены:  ",        report.removed)
    print("Изменены (код):",   report.modified)            # hash-based (issue #38)
    print("Изменены (разм.):", report.structure_modified)  # elem hash  (issue #40)

# 1) распаковываем все формы выгрузки
artifacts = unpack_all_forms(dump_root, unpacked_root, unpack_one)

# 2) обновляем карту актуальности
index = update_forms_index(dump_root, unpacked_root, artifacts)
index.save(Path("forms_index.json"))

# позже — узнаём, какие формы устарели и требуют перераспаковки
for name in index.stale_forms():
    print("устарела:", name)
```

Полный пример: [`examples/basic_usage.py`](examples/basic_usage.py).

## Тесты

```bash
pip install -e ".[test]"
pytest
```

Набор тестов полностью синтетический: проверка идёт на временных файловых
деревьях с внедрённым распаковщиком-заглушкой, так что реальный контейнер 1С
не требуется.

## Связанное

- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — нижележащий распаковщик контейнеров (Python, MIT)
- [PR#29 — fix: add ExternalReport (.erf) support](https://github.com/saby-integration/v8unpack/pull/29) — принят
- [Обычные формы 1С в агентном пайплайне: пошаговая распаковка](https://infostart.ru/1c/articles/2721726/)
- [СКД и дерево элементов обычной формы 1С: два некритичных шага в агентном пайплайне](https://infostart.ru/1c/articles/2726561/)

## Лицензия

MIT
