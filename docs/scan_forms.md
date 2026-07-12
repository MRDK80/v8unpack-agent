# Сканер форм (scan_forms)

`scan_forms(cf_export_root)` обходит дерево выгрузки и строит `FormScanIndex` —
опись всех форм с их артефактами (`.obj.bsl` + `.json`) без парсинга содержимого.
Это **нулевой шаг пайплайна**: сначала узнаём что есть, потом распаковываем.

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms

root  = Path("/path/to/cf_export")
index = scan_forms(root, save_to=Path("forms_scan_index.json"))

print(f"Найдено форм: {index.total}")
for entry in index.forms:
    print(entry.container_name, entry.object_name, entry.form_name)
```

## CLI

    python -m v8unpack_agent.scan_forms <root> [--mode {config,external}] [--save]

- `--mode config` (по умолчанию) — структура конфигурации;
- `--mode external` — распакованные внешние обработки и отчёты (issues #25, #32);
- `--save` — сохранить `forms_scan_index.json` в `<root>`.

## Layout выгрузки v8unpack

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

**3-уровневый** (CommonForm — общие формы конфигурации, нет уровня `ObjectName`):

```
<root>/
  CommonForm/               # одновременно ObjectType и ContainerName
    <FormName>/             # НастройкаПрограммы, ВыборСертификата, …
      CommonForm.obj.bsl
      CommonForm.json
```

### Как различаются layout

Оба layout обходятся одним структурным правилом: контейнер форм — каталог,
чьё имя `endswith("Form")`. **Нет хардкода конкретных имён контейнеров.**
Различие — только в глубине вложенности: в 4-уровневом есть промежуточный
уровень `ObjectName` между типом и контейнером, в 3-уровневом он
отсутствует (контейнер совпадает с корневым типом).

Сводный маппинг полей `FormEntry` для обоих layout:

| Layout | Пример | `object_type` | `object_name` | `container_name` |
|--------|--------|---------------|---------------|------------------|
| 4-уровневый | `Catalog/Склады/CatalogForm/ФормаЭлемента` | `Catalog` | `Склады` | `CatalogForm` |
| 4-уровневый | `Document/Акт/DocumentForm/ФормаВыбора` | `Document` | `Акт` | `DocumentForm` |
| 3-уровневый | `CommonForm/НастройкаПрограммы` | `CommonForm` | `""` (пустая) | `CommonForm` |

> **Связь с движком.** Это доменная (1С-специфичная) реализация generic-паттерна,
> описанного в приватном движке [`llm-dev-engine#70`](https://github.com/MRDK80/llm-dev-engine/issues/70):
> ядро детектит отсутствие промежуточного уровня структурно (без литералов имён),
> а конкретика `CommonForm` живёт здесь, в доменном репо (см. #49).

## Layout внешних обработок и отчётов (mode="external")

См. подробное описание структуры файлов, маппинга и поддержки версий:
[docs/external_forms_structure.md](external_forms_structure.md).

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms

index = scan_forms(Path("/path/to/External"), mode="external",
                   save_to=Path("forms_scan_index.json"))
```

## Семантика контейнеров

| Контейнер | Типы объектов |
|---|---|
| `Form` | `DataProcessor` (внутри `.cf`) и `ExternalDataProcessor` (`.epf`) — различать по `object_type` |
| `ReportForm` | `Report` (`.cf`) и `ExternalReport` (`.erf`) — различать по `object_type` |
| `CatalogForm`, `DocumentForm`, `InformationRegisterForm`, … | однозначно определяются именем контейнера |
| `CommonForm` | общие формы, 3-уровневый layout без уровня `ObjectName` (`object_name = ""`) |

## FormEntry

`FormEntry` — dataclass, результат обхода одной формы:

| Поле | Тип | Значение |
|---|---|---|
| `object_type` | string | Тип объекта: `Catalog`, `Document`, `DataProcessor`, … Для external — `ExternalDataProcessor` / `ExternalReport`. |
| `object_name` | string | Имя объекта. **Пустая строка `""` для `CommonForm`** (3-уровневый layout — нет уровня-владельца); для external — имя обработки/отчёта |
| `container_name` | string | Имя контейнера форм: `CatalogForm`, `Form`, `ReportForm`, `CommonForm`, … |
| `form_name` | string | Имя формы: `ФормаЭлемента`, `ФормаСписка`, … |
| `form_path` | string | Путь к директории формы относительно корня выгрузки |
| `bsl_path` | string | Путь к bsl-файлу формы (`<Container>.obj.bsl` или legacy `<Container>.obj`) |
| `json_path` | string | Путь к `.json` относительно корня выгрузки |
| `bsl_mtime` | float | `st_mtime` bsl-файла на момент сканирования. Legacy fallback для старых индексов без `bsl_sha256`. `0.0` — неизвестно. |
| `bsl_sha256` | string \| null | SHA-256 содержимого bsl-файла. Основной критерий `modified` в `check_drift()` (issue #38). `null` в старых индексах → legacy fallback через `bsl_mtime`. |
| `elem_sha256` | string \| null | SHA-256 нормализованного дерева элементов формы (issue #40). Хэшируются только структурно значимые поля: `name`, `type`, `path`, `parent`, `parent_path`, `page`, `source`, `data_path`, `handler`. Косметика (координаты, цвета, шрифты, GUID) исключена. `null` — `*.elem.json` не найден или список пуст. |
| `form_elem_path` | string \| null | Путь к `Form.elem` (mode="external"). `null` для форм конфигурации или если файла нет. |
| `warnings` | array | Предупреждения (обычно пусто) |

## FormScanIndex

`FormScanIndex` содержит список `forms`, счётчик `total`, метку `scanned_at` и
список `scan_warnings` (пропущенные формы без bsl-файла).

Загрузка сохранённого индекса:

```python
from v8unpack_agent.scan_forms import FormScanIndex

index = FormScanIndex.load(Path("forms_scan_index.json"))
# Старые индексы без bsl_sha256 / elem_sha256: поля получают None (backward-compat).
# Старые индексы без bsl_mtime: поле получает 0.0 (backward-compat).
```

## Поведение при ошибках

- Форма без bsl-файла → `skipped (no <Container>.obj.bsl / <Container>.obj): <path>`
  в `scan_warnings`, в индекс не попадает.
- Ошибка в одной форме не останавливает обход (best-effort).

## Конвенция путей (формы конфигурации)

```
<unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
<unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
<unpacked_root>/Form/<имя>/Form.json             # метаданные формы
<unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
```
