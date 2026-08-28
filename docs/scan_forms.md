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

Поддерживаемые виды метаданных перечислены в разделе
[«Границы резолюции ссылочных типов»](#границы-резолюции-ссылочных-типов);
таблица там синхронизирована с `REFERENCE_TYPE_PREFIXES` guard-тестом (#168).

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

## Границы резолюции ссылочных типов

### Что индексируется

`scan_forms()` во время обхода выгрузки строит `FormScanIndex.reference_types`.
Ключ записи — UUID ссылочной грани типа, значение — `<ReferencePrefix>.<ObjectName>`.
Имя строится только для видов метаданных, перечисленных в
`REFERENCE_TYPE_PREFIXES`; для остальных видов имя ссылочного типа не строится
по замыслу.

Обход не угадывает семантику UUID и не сопоставляет типы по именам реквизитов.
UUID, отсутствующий в индексе, возвращается `resolve_reference_type()` как
безопасный fallback `Ref#uuid`.

<!-- reference-type-prefixes:start -->
| Вид метаданных | Префикс ссылочного типа |
|---|---|
| `BusinessProcess` | `BusinessProcessRef` |
| `Catalog` | `CatalogRef` |
| `ChartOfCharacteristicType` | `ChartOfCharacteristicTypeRef` |
| `Document` | `DocumentRef` |
| `Enum` | `EnumRef` |
| `ExchangePlan` | `ExchangePlanRef` |
| `Task` | `TaskRef` |
<!-- reference-type-prefixes:end -->

Строки упорядочены по ключу — этот порядок проверяет
`tests/test_scan_forms_reference_type_docs_issue168.py`. Маркеры и формат ячеек
(`` `Value` ``) нужны guard-тесту: не менять их, не изменив тест одновременно.

### Несколько типовых граней одного объекта

Файл объекта может объявлять несколько UUID типовых граней. Индексируется только
доказанная ссылочная грань; остальные UUID того же объекта не обязаны иметь
ссылочное имя. Наличие объекта в индексе не означает, что каждый UUID его
identity- или header-блока должен резолвиться.

Попытка подписать дополнительную грань именем ссылочного типа даёт
ложноположительную резолюцию, поэтому она запрещена контрактом.

```
header/.../reference-slot     → индексируется
header/.../other-type-slot    → остаётся Ref#uuid
```

### Типы, определяемые платформой

`reference_types` строится исключительно из файлов выгрузки конфигурации. Тип,
определяемый платформой и не имеющий позиции определения в выгрузке, не может
попасть в индекс через расширение обхода. Статической таблицы платформенных UUID
в текущем контракте нет.

Исследование #143 сформулировало гипотезу платформенной природы остатка.
Кросс-конфигурационная проверка #164 выполнена на второй независимой выгрузке и
подтвердила гипотезу частично: 6 UUID из 20 класса `reference_only` входят в
остаток обеих выгрузок при валидных позитивных контролях (покрытие 100% в
каждой), что покрывает 868 из 1143 `reference_only`-вхождений (75.94%) и 5.52%
всех применимых ссылочных вхождений. Для 14 UUID подтверждения нет; опровержений
не получено: у подтверждённых элементов не найдено ни определений, ни попаданий
в индекс типов. Итог: `partially_confirmed`, агрегаты в
`docs/research/platform_types_cross_config_issue164.md`.

Совпадение UUID доказывает стабильность идентификатора между конфигурациями, но
не человекочитаемое имя типа. Поэтому #165 остаётся заблокированной до отдельного
авторитетного доказательства имён; потолок эффекта по доказанному объёму —
+5.52 п.п. к 91.72% (до 97.24%), а не +7.27 п.п.

### Когда `Ref#uuid` — норма, а когда повод для RCA

| Ситуация | Интерпретация | Действие |
|---|---|---|
| Вид отсутствует в `REFERENCE_TYPE_PREFIXES` | Ожидаемая граница контракта | Сохранить `Ref#uuid`; расширять только через отдельный доказанный контракт |
| UUID — нессылочная грань объекта, чья ссылочная грань уже индексируется | Ожидаемый результат | Сохранить `Ref#uuid` |
| Кандидат платформенного типа отсутствует в файлах выгрузки | Недостаточно доказательств | Сохранить fallback; проверять через #164 |
| UUID присутствует в `reference_types`, но резолвер возвращает `None` | Несогласованность | RCA |
| Вид входит в `REFERENCE_TYPE_PREFIXES`, но у объекта не проиндексирован ни один доказанный ссылочный слот | Потенциальный дефект индекса или layout | RCA |
| Один UUID сопоставлен разным объектам или именам | Конфликт | RCA; не выбирать значение эвристически |
| Позиция определения неоднозначна | Недостаточно доказательств | Сохранить `Ref#uuid` |

Критерий из #143 в явном виде:

> Повод для RCA: вид входит в `REFERENCE_TYPE_PREFIXES`, но ни один доказанный
> ссылочный слот файла объекта не попал в индекс.

По результатам #143 таких аномалий не обнаружено.

### Измеренный результат #143

```
definition_known:
  28 UUID / 158 вхождений
  - 18 UUID / 103 вхождения: виды вне REFERENCE_TYPE_PREFIXES
  - 10 UUID / 55 вхождений: нессылочные типовые грани

reference_only:
  20 UUID / 1143 вхождения
  гипотеза платформенных типов не подтверждена

аномалии индекса:
  0

решение:
  keep unresolved
```

Подробности и воспроизведение: [`docs/research/ref_resolver_issue143.md`](research/ref_resolver_issue143.md),
[`examples/unresolved_refs_report.py`](../examples/unresolved_refs_report.py),
issues #143, #164, #165, #166.

### Итог исследования #166: нессылочные типовые грани

Числовые слоты `header/0/1/N`, в которых стоят UUID класса `definition_known`,
исследованы на двух независимых выгрузках (#166, отчёт
`docs/research/non_reference_type_facets_issue166.md`).

- Позиции слотов устойчивы внутри одной выгрузки: 9 пар «вид × слот», 28 UUID,
  158 вхождений; слот встречается один раз на объект и не совпадает с
  проиндексированной ссылочной гранью.
- Имена граней не доказаны: документированного формата raw-header и
  авторитетного описания слотов нет, синтетическая конфигурация не строилась,
  имена соседних реквизитов как источник запрещены.
- Пересечение пар «вид × слот» между двумя выгрузками пусто, поэтому
  кросс-конфигурационная стабильность не подтверждена.
- Решение: `keep unresolved`. UUID остаются `Ref#uuid`, `REFERENCE_TYPE_PREFIXES`
  не расширен, отдельная таблица имён граней не вводится.
- DocumentJournal, InformationRegister, Report и DataProcessor не добавляются в
  таблицу префиксов: она означает наличие доказанной ссылочной формы, которой у
  этих видов нет.
- Исследовательские прогоны агрегируют `scan_warnings` через
  `scan_warning_code()` (#167): A — 49 записей, 0 без кода, `FORM_MODULE_MISSING`;
  B — 18 записей, 0 без кода, `FORM_MODULE_MISSING`.

## Машинные коды scan_warnings (issue #167)

Машинный контракт — `code`, а не свободный текст сообщения. Текст предназначен
для человека и может уточняться без изменения причины.

- Формат записи: `<существующий текст> [code=UPPER_SNAKE]`; тип поля остаётся
  `list[str]`, JSON-формат не меняется, миграция старых индексов не нужна.
- Публичный парсер: `v8unpack_agent.scan_forms.scan_warning_code(warning)`.
- Legacy-запись без суффикса, повреждённый суффикс и код вне перечня дают `None`;
  строки старых индексов при загрузке не переклассифицируются.
- Порядок предупреждений детерминирован; стабильность свободного текста не
  гарантируется, стабильность кода гарантируется.
- `FormEntry.warnings` (например, `elem-only: no .obj.bsl found`) этим контрактом
  не покрыт: у пер-форменных предупреждений отдельный контракт.
- Связь с #143: агрегация остатка выполнялась по подстрокам, теперь возможна по коду.

<!-- scan-warning-codes:start -->
| Код | Условие |
|---|---|
| `ELEM_DISCOVERY_UNAVAILABLE` | не удалось импортировать `discover_elem_forms`; elem-only формы не добавлены |
| `FORM_MODULE_MISSING` | в каталоге формы нет ожидаемого BSL-файла (config `.obj.bsl` либо external-кандидаты) |
| `FORM_SCAN_ERROR` | исключение при обходе каталога формы; обход продолжается (best-effort) |
| `REFERENCE_METADATA_INCOMPLETE` | блок идентификации объекта не содержит UUID |
| `REFERENCE_UUID_CONFLICT` | один UUID указывает на разные имена типов; сохранена первая запись |
| `SCAN_ROOT_INVALID` | `cf_export_root` не существует или не является каталогом |
<!-- scan-warning-codes:end -->

<!-- issue-180-followup -->
## Follow-up: диагностика на третьей конфигурации (#180)

Проверка на третьей независимой выгрузке (3 738 форм) подтверждает контракты после #167 и #172.

- `scan_warnings`: 69 всего, `without_code = 0`, единственный код `FORM_MODULE_MISSING`.
- Формы без `object_attributes`: 113 из 3 738; разрез однороден на 100% —
  точка отказа `object_json_not_found`, класс причины `no_owner_object`, `object_type = CommonForm`,
  класс layout `rel_depth=2/object_name:absent`, роль найденного JSON `absent`, `FormClass = service`,
  `DecodeError` пуст, путей-кандидатов 0.
- Число 113 совпадает с числом файлов вида `CommonForm` в выгрузке: общие формы не имеют
  объекта-владельца, остаток объясняется полностью.
- Регрессии #172 нет: `export_root_neighbour` не воспроизводится, корневой JSON не подставляется.
- Скрининг непроиндексированных форм: indexed 3 680, `no_tabular_no_widgets` 56,
  `tabular_field_programmatic_no_defs` 2; `Form.bin` не найден → screening #150 не применим.

Подпись агрегата отчёта по формам `04bdab25052cf9d8`, два прогона идентичны.
Полный отчёт: `docs/research/third_configuration_validation.md`.
