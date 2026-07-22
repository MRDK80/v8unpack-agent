# Сканер форм (scan_forms)

`scan_forms(cf_export_root)` обходит дерево выгрузки и строит `FormScanIndex` —
опись всех форм по layout-у выгрузки. Это **нулевой шаг пайплайна**: сначала
узнаём что есть, потом распаковываем.

Начиная с issue #57 индекс включает и формы **без кода** (без `.obj.bsl`):
управляемые формы, где вся логика наследуется от типовых механизмов, но
всегда присутствует `*.elem.json`. Такие формы подбираются elem-only веткой и
получают заполненное поле `elem_json_path` (см. ниже).

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms

root  = Path("/path/to/cf_export")
index = scan_forms(root, save_to=Path("forms_scan_index.json"))

print(f"Найдено форм: {index.total}")
for entry in index.forms:
    print(entry.container_name, entry.object_name, entry.form_name)
```

## Сигнатура

```python
def scan_forms(
    cf_export_root: Path,
    save_to: Optional[Path] = None,
    mode: Literal["config", "external"] = "config",
    include_elem_only: bool = True,
) -> FormScanIndex: ...
```

| Параметр | По умолчанию | Значение |
|---|---|---|
| `cf_export_root` | — | Корень выгрузки (config) либо каталог с `External/` (external) |
| `save_to` | `None` | Если задан — сохранить JSON-индекс в этот файл |
| `mode` | `"config"` | `config` — структура конфигурации; `external` — распакованные внешние обработки/отчёты (issues #25, #32) |
| `include_elem_only` | `True` | Добавлять elem-формы без `.obj.bsl`, обнаруженные через `discover_elem_forms` (issue #57) |

## CLI

    python -m v8unpack_agent.scan_forms <root> [--mode {config,external}] [--save] [--no-elem-only]

- `--mode config` (по умолчанию) — структура конфигурации;
- `--mode external` — распакованные внешние обработки и отчёты (issues #25, #32);
- `--save` — сохранить `forms_scan_index.json` в `<root>`;
- `--no-elem-only` — не добавлять elem-only формы (управляемые без `.obj.bsl`).

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
Различие — только в глубине вложенности: в 4-уровневом е��ть промежуточный
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

## Формы без кода (elem-only, issue #57)

v8unpack материализует каждую форму как `*.elem.json`, но `.obj.bsl` создаётся
только если у формы есть собственный код модуля. Управляемые формы, целиком
наследующие поведение от типовых механизмов (`ФормаСписка`, `ФормаВыбора`,
`ФормаЗаписи` и т.п.), кода не имеют — `.obj.bsl` для них отсутствует.

При `include_elem_only=True` (по умолчанию) `scan_forms`:

1. Выполняет основной обход (config или external) — формы с `.obj.bsl`.
2. Через `discover_elem_forms` находит все `*.elem.json`.
3. Формы, ещё не добавленные основным обходом, включает в индекс как elem-only:
   - `elem_json_path` заполнен (relative-to-root);
   - `bsl_sha256 = None`, `bsl_mtime = 0.0`;
   - `warnings = ["elem-only: no .obj.bsl found"]`;
   - `object_type` / `object_name` / `container_name` / `form_name`
     восстанавливаются из пути формы с учётом `mode`.

Метаданные elem-only форм выводятся из relative-пути:

| `mode` | Layout пути | Пример | Результат |
|--------|-------------|--------|-----------|
| `config` | `<type>/<object>/<container>/<form>` | `Report/Отчет/ReportForm/ФормаОтчета` | `Report` / `Отчет` / `ReportForm` / `ФормаОтчета` |
| `config` | `<container>/<form>` (CommonForm) | `CommonForm/ФормаВыбора` | `""` / `""` / `CommonForm` / `ФормаВыбора` |
| `external` | `<object>.erf/ReportForm/<form>` | `report.erf/ReportForm/ФормаОтчета` | `ExternalReport` / `report.erf` / `ReportForm` / `ФормаОтчета` |
| `external` | `<object>.epf/Form/<form>` | `proc.epf/Form/Форма` | `ExternalDataProcessor` / `proc.epf` / `Form` / `Форма` |

> **Фикс метаданных external elem-only (issue #57).** До фикса elem-only ветка
> разбирала external-путь как config-layout, из-за чего внешний управляемый
> отчёт без кода получал искажённые метаданные (пустые `object_type` /
> `object_name`, `form_name`, совпадающий с контейнером). Теперь для `mode="external"`
> путь `<object>.(epf|erf)/(Form|ReportForm)/<form>` разбирается корректно,
> а `object_type` выводится по контейнеру (`ReportForm` ⇒ `ExternalReport`) и
> расширению (`.erf` ⇒ `ExternalReport`, иначе `ExternalDataProcessor`).

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
| `object_type` | string | Тип объекта: `Catalog`, `Document`, `DataProcessor`, … Для external — `ExternalDataProcessor` / `ExternalReport`. Для elem-only восстанавливается из пути. |
| `object_name` | string | Имя объекта. **Пустая строка `""` для `CommonForm`** (3-уровневый layout — нет уровня-владельца); для external — имя обработки/отчёта |
| `container_name` | string | Имя контейнера форм: `CatalogForm`, `Form`, `ReportForm`, `CommonForm`, … |
| `form_name` | string | Имя формы: `ФормаЭлемента`, `ФормаСписка`, … |
| `form_path` | string | Путь к директории формы относительно корня выгрузки |
| `bsl_path` | string | Путь к bsl-файлу формы (`<Container>.obj.bsl` или legacy `<Container>.obj`). Для elem-only — путь-заглушка на несуществующий файл. |
| `json_path` | string | Путь к `.json` относительно корня выгрузки. Для elem-only — путь-заглушка. |
| `bsl_mtime` | float | `st_mtime` bsl-файла на момент сканирования. Legacy fallback для старых индексов без `bsl_sha256`. `0.0` — неизвестно (в т.ч. для elem-only). |
| `bsl_sha256` | string \| null | SHA-256 содержимого bsl-файла. Основной критерий `modified` в `check_drift()` (issue #38). `null` в старых индексах и у elem-only форм → legacy fallback через `bsl_mtime`. |
| `elem_sha256` | string \| null | SHA-256 нормализованного дерева элементов формы (issue #40). Хэшируются только структурно значимые поля: `name`, `type`, `path`, `parent`, `parent_path`, `page`, `source`, `data_path`, `handler`. Косметика (координаты, цвета, шрифты, GUID) исключена. `null` — `*.elem.json` не найден или список пуст. |
| `form_elem_path` | string \| null | Путь к `Form.elem` (mode="external"). `null` для форм конфигурации или если файла нет. |
| `elem_json_path` | string \| null | Путь к `*.elem.json` относительно корня выгрузки (issue #57). Согласован с `ElemFormEntry.elem_json_path` (issue #55). Заполнен для ordinary/external форм, если `*.elem.json` присутствует в каталоге; всегда заполнен для elem-only форм. `null` в старых индексах. Реестр хранит только путь; структуру по требованию даёт `parse_elem_json` (второй парсер не вводится). |
| `warnings` | array | Предупреждения. Для elem-only форм содержит `"elem-only: no .obj.bsl found"`. |

## FormScanIndex

`FormScanIndex` содержит список `forms`, счётчик `total`, метку `scanned_at` и
список `scan_warnings`.

Загрузка сохранённого индекса:

```python
from v8unpack_agent.scan_forms import FormScanIndex

index = FormScanIndex.load(Path("forms_scan_index.json"))
# Старые индексы без bsl_sha256 / elem_sha256 / elem_json_path:
#   соответствующие поля получают None (backward-compat).
# Старые индексы без bsl_mtime: поле получает 0.0 (backward-compat).
# Поле form_xml_path в старых индексах игнорируется.
```

## Поведение при ошибках

- Форма без bsl-файла:
  - при `include_elem_only=True` (по умолчанию) — подбирается elem-only веткой
    и попадает в индекс с заполненным `elem_json_path` (issue #57);
  - при `include_elem_only=False` — пропускается: запись
    `skipped (no <Container>.obj.bsl / <Container>.obj): <path>` в `scan_warnings`,
    в индекс не попадает.
- Ошибка в одной форме не останавливает обход (best-effort).

## Конвенция путей (формы конфигурации)

```
<unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
<unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
<unpacked_root>/Form/<имя>/Form.json             # метаданные формы
<unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
```
