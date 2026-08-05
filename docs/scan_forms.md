# Сканер форм (scan_forms)

`scan_forms(cf_export_root)` обходит дерево выгрузки и строит `FormScanIndex` —
опись всех форм по layout-у выгрузки. Это **нулевой шаг пайплайна**: сначала
узнаём что есть, потом распаковываем.

Начиная с issue #57 индекс включает и формы **без кода** (без `.obj.bsl`):
управляемые формы, где вся логика наследуется от типовых механизмов, но
всегда присутствует `*.elem.json`. Такие формы подбираются elem-only веткой и
получают заполненное поле `elem_json_path` (см. ниже).

Начиная с issue #88 тот же обход конфигурации попутно строит индекс ссылочных
типов `uuid → имя ссылочного типа` (`reference_types`). Второй обход дерева не
вводится (см. ниже).

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

## Индекс ссылочных типов (issue #88)

Реквизиты объектов метаданных ссылаются на другие объекты по UUID, а не по
имени. `object_decoder` возвращает такой тип как `Ref#<uuid>` — достоверно, но
нечитаемо. Приведение UUID к имени требует **глобального** знания о выгрузке,
которое обход `scan_forms` уже имеет.

Поэтому индекс собирается в той же точке, где обход и так перебирает каталоги
объектов — отдельного discovery нет, дерево читается один раз. В режиме
`mode="external"` индекс не собирается: у внешних объектов нет каталогов
метаданных конфигурации.

```python
from pathlib import Path
from v8unpack_agent.scan_forms import scan_forms
from v8unpack_agent.object_decoder import decode_object_attributes

index = scan_forms(Path("/path/to/cf_export"))

print(len(index.reference_types))                       # размер индекса
print(index.resolve_reference_type("<uuid>"))           # "CatalogRef.Города" | None

result = decode_object_attributes(
    Path("/path/to/cf_export/Catalog/Города/Catalog.json"),
    type_resolver=index.resolve_reference_type,
)
```

Как строится индекс:

- UUID берутся из блока идентификации объекта `header[0][1]`, слоты 1–3.
  У объекта метаданных несколько идентификаторов, и ссылка реквизита адресует
  не обязательно слот 2 — на контрольной выгрузке встречаются слоты 1 и 3.
  Все они принадлежат одному объекту, поэтому имя типа для них одно.
- Имя строится как `<Префикс>.<ИмяОбъекта>` по таблице
  `REFERENCE_TYPE_PREFIXES`.
- Порядок обхода отсортирован, дубликаты внутри объекта убираются —
  результат детерминирован.
- Первая запись по UUID сохраняется; конфликт разных объектов по одному UUID
  попадает в `scan_warnings` как `reference type duplicate UUID …`.
- Неполные или нечитаемые метаданные дают
  `reference type metadata is incomplete: …` в `scan_warnings`; исключение не
  выбрасывается, тип не угадывается.

Поддерживаемые виды метаданных:

| Вид | Префикс имени |
|---|---|
| `Catalog` | `CatalogRef` |
| `Document` | `DocumentRef` |
| `Enum` | `EnumRef` |
| `ChartOfCharacteristicType` | `ChartOfCharacteristicTypeRef` |
| `ExchangePlan` | `ExchangePlanRef` |
| `BusinessProcess` | `BusinessProcessRef` |
| `Task` | `TaskRef` |

Регистры в таблицу не входят: ссылочного типа у них нет. Виды, отсутствующие
в таблице, в индекс не попадают, а их UUID остаются безопасным `Ref#<uuid>`.

Результат на контрольной выгрузке: индекс из 2230 записей, 4670 из 5226
ссылочных реквизитов получили читаемое имя, 556 остались `Ref#<uuid>`;
изменённых нессылочных записей, потерь и исключений — 0. Подробности —
[`object_decoder`](object_decoder.md).

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

`FormScanIndex` содержит список `forms`, счётчик `total`, метку `scanned_at`,
список `scan_warnings` и индекс `reference_types`.

| Поле | Тип | Значение |
|---|---|---|
| `forms` | list\[FormEntry\] | Найденные формы |
| `total` | int | Количество форм |
| `scanned_at` | string | Метка времени сканирования (UTC, ISO 8601) |
| `scan_warnings` | array | Предупреждения обхода, включая диагностику индекса ссылочных типов |
| `reference_types` | dict\[str, str\] | `uuid → имя ссылочного типа` (issue #88). Пустой словарь для `mode="external"` и для старых индексов. |

Метод `resolve_reference_type(uuid)` возвращает имя ссылочного типа либо
`None`. Подпись совместима с параметром `type_resolver` функции
`decode_object_attributes`, поэтому индекс передаётся в декодер напрямую.

Загрузка сохранённого индекса:

```python
from v8unpack_agent.scan_forms import FormScanIndex

index = FormScanIndex.load(Path("forms_scan_index.json"))
# Старые индексы без bsl_sha256 / elem_sha256 / elem_json_path:
#   соответствующие поля получают None (backward-compat).
# Старые индексы без bsl_mtime: поле получает 0.0 (backward-compat).
# Старые индексы без reference_types: поле получает {} (backward-compat).
# Поле form_xml_path в старых индексах игнорируется.
```

## Поведение при ошибках

- Форма без bsl-файла:
  - при `include_elem_only=True` (по умолчанию) — подбирается elem-only веткой
    и попадает в индекс с заполненным `elem_json_path` (issue #57);
  - при `include_elem_only=False` — пропускается: запись
    `skipped (no <Container>.obj.bsl / <Container>.obj): <path>` в `scan_warnings`,
    в индекс не попадает.
- Нечитаемые или неполные метаданные объекта не прерывают обход: UUID не
  попадает в индекс, в `scan_warnings` пишется причина (issue #88).
- Ошибка в одной форме не останавливает обход (best-effort).

## Конвенция путей (формы конфигурации)

```
<unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
<unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
<unpacked_root>/Form/<имя>/Form.json             # метаданные формы
<unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
```
