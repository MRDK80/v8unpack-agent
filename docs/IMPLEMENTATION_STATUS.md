# Статус реализации

## Реализовано

### scan_forms
- Config-layout: 4-уровневый (`<Тип>/<Объект>/<Контейнер>/<Форма>/`) и
  3-уровневый (`CommonForm/<Форма>/`) — issues #9, #13
- External-layout: `External/<объект>/<Form|ReportForm>/<форма>/` — issues #25, #32
- Elem-only формы (управляемые без `.obj.bsl`) — issues #55, #57
- `bsl_sha256` в FormEntry — issue #38
- `elem_sha256` в FormEntry — issue #40
- `elem_json_path` (relative-to-root) в FormEntry — issue #57

### drift_checker
- `check_drift()` с `DriftReport`: added / removed / modified /
  stale_extractions / structure_modified — issues #10, #18, #38, #40
- Hash-based `modified` detection — issue #38
- Elem-only формы исключены из removed/stale, включены в structure_modified
  — issue #58
- **`check_drift(mode="external")`**: корректная поддержка external-layout
  через делегирование `_disk_snapshot` → `scan_forms(mode=mode)` — issue #73.
  Проверено на 14 реальных внешних формах (ExternalDataProcessor + ExternalReport).

### catalog_resolver
- `resolve_data_path(data_path, object_json)` — best-effort резолюция строки
  `data_path` через JSON объекта (`Catalog.json` и др.); поддерживает пути
  `Объект.Реквизит` и `Объект.ТЧ.Реквизит`; при отсутствии файла или
  нераспознанном пути возвращает `resolved=False` без исключений — issue #76,
  PR #83.
- `object_json_path(form_entry)` — определяет путь к JSON объекта по
  `form_entry.form_path` (2 уровня вверх).
- После #84 `resolved=True` достижим на реальных выгрузках: читаемые
  `Properties` и `TabularSections` поступают из `object_decoder`.

### object_decoder — реквизиты объекта из raw `header`
- `decode_object_attributes(object_json)` + `DecodeResult` — декодирование
  raw-секции `header` в `{"Properties": [...], "TabularSections": [...]}`:
  имя, UUID и тип реквизита, табличные части с колонками — issue #84, PR #87.
- Три стратегии по убыванию точности: production-layout со строковыми тегами,
  compact owner-metadata (`header[0][6]`), legacy walker с целочисленными
  тегами и ограничением глубины.
- Примитивы (`String`, `Number`, `Boolean`, `Date`) разрешаются через таблицу
  кодов; ссылочные типы возвращаются как `Ref#<uuid>` — приведение к имени
  объекта метаданных вынесено в #88.
- UUID-карта `elem_parser` строится из результата этого модуля; дублирующая
  рекурсивная реализация обхода `header` удалена.
- Best-effort: повреждённый узел пропускается с записью в `warnings`,
  исключения не пробрасываются.
- Прогон на 6551 объекте метаданных: 15888 реквизитов, 0 нераспознанных кодов
  типа, 0 ошибок декодирования; 14134 привязки `data_path` — без регресса
  относительно состояния до PR.

### elem_parser — привязка элементов к данным
- `decode_legacy_data_path()` + `is_legacy_form_data()` — декодирование
  `data_path` в **обычных** формах через поле `prop` записи `data`
  (имя реквизита в `raw[4][1]` при теге `"14"`) — issue #85, PR #86.
- Разбор по UUID реквизита оставлен только для управляемых форм; в обычных
  UUID внутри `raw` описывают класс виджета и давали ложные срабатывания.
- Для управляемых форм добавлен консервативный структурный fallback после
  UUID: точное имя реквизита формы (с рекурсивным обходом вложенных `props`)
  и колонка таблицы через непосредственного родителя. Префиксные эвристики
  не применяются — issue #85, PR #86.
- `_merge_source_duplicates()` — схлопывание одноимённых записей `data`/`props`
  в одну (приоритет `data`, слитые источники в `merged_sources`).
- `load_owner_attribute_map()` построена поверх `object_decoder` — issue #84,
  PR #87.
- `extract_legacy_form_elements()` + `_find_legacy_form_json()` —
  fallback-чтение обычных форм с пустым `tree` (`*.elem.json`) через отдельный
  `form/*.json` (ФормаЗаписи, ФормаЭлемента, формы обработок и отчётов).
  Подняло 49 форм из статуса ERROR (issue #100, PR #102).
- Проверено на `Catalog/Банки`: `ФормаЭлемента` — 8 привязок из 20 элементов,
  `ФормаСписка` — 4 из 7, `ФормаЭлементаУправляемая` — 11 из 19, warnings 0.
- Аудит структурного fallback на реальной выгрузке: 3292 из 4549 исходно
  неразрешённых Field-записей безопасно восстановлены, 1257 оставлены без
  догадок; 81 дополнительное точное совпадение подтверждено во вложенных
  `props`. Метрика относится только к проверенной выгрузке.
- `extract_legacy_list_form_elements()` — fallback для legacy
  `ФормаСписка` / `ФормаВыбора` с пустым `tree` и `TabularField`.
  Поддерживает упорядоченные слоты блока `"20"` и точные ссылки
  `["0", UUID]`; UUID разрешаются только через карту реквизитов владельца.
  Блок `"20"` имеет приоритет, неоднозначные слоты не переводятся на менее
  строгий fallback. Результат: `TabularFieldColumn` с путём
  `<Источник>.<Реквизит>` (issue #103).
- Верификация #103: 487 тестов; полный проход по 2216 формам — OK 2169,
  fallback #103 160, ERROR 77, исключений 0, невалидных результатов 0.

- `classify_unindexed_form()` + `UnindexedReason` / `UnindexedResult` —
  диагностика форм, оставшихся с `elem_index_ok=False`. Причина возвращается
  машиночитаемым enum, `data_path` не создаётся, `ElemIndexResult` не мутируется,
  исключения не пробрасываются (issue #105, PR #106).
- Верификация #105 на 2216 формах: проиндексировано 2169 (97.9%),
  неиндексировано 47 — `NO_TABULAR_NO_WIDGETS` 17, `TABULAR_FIELD_BSL_SOURCE_MISMATCH` 11, `TABULAR_FIELD_PROGRAMMATIC_NO_DEFS` 8, `TABULAR_FIELD_PLATFORM_DYNAMIC` 7,
  `TABULAR_FIELD_EMPTY_ATTR_MAP` 2, `NO_LEGACY_JSON` 0, `UNKNOWN` 0.
  Категория A целиком — `CommonForm` и `ChartOfCharacteristicType`:
  у `CommonForm` объекта-владельца нет, пустая карта реквизитов правомерна.
  17 тестов в `tests/test_elem_parser_issue105.py`, полная регрессия зелёная.

### coverage_metric — метрика покрытия data_path
- `calc_data_path_coverage(elements)` + `CoverageReport` — двухслойная
  метрика покрытия `data_path`. Знаменатель включает только элементы данных
  (`DATA_ELEMENT_TYPES`): `Field`, `InputField`, `Table`, `CheckBox`,
  `Calendar`, `Chart`, `Picture`. Служебные элементы (`Label`, `CommandPanel`,
  `Panel`, `Page`, `Group`, `Button`, `Separator`) исключены из знаменателя и
  вынесены в именованную константу `SERVICE_ELEMENT_TYPES`.
- `calc_coverage_from_elem_index(result)` — обёртка под `ElemIndexResult`
  из `elem_parser`.
- `PLATFORM_STANDARD_ATTRIBUTES` — frozenset стандартных реквизитов платформы
  (`Код`, `Наименование`, `Родитель`, `Дата`, `Номер`, `ПометкаУдаления`);
  их корректная привязка зависит от резолюции `Ref#uuid` — тема #88.
- `CoverageReport` хранит обе метрики: `bound_data_elements / data_elements`
  (покрытие по полям данных) и `total_elements` (все элементы формы) для
  справки. Поддерживает `__str__` и `to_dict()` для JSON-отчётов.
- Проверено на реальных выгрузках:
  `Catalog/Банки/ФормаЭлементаУправляемая` — 11/14 = 78.6%
  (3 неразрешённых — платформенные реквизиты, ждут #88);
  `Catalog/Контрагенты/ФормаЭлемента` — 45/45 = 100.0% — issue #90, PR #97.
- Поле `CoverageReport.form_class` и параметр `form_name`
  в `calc_data_path_coverage()` (issue #98, PR #99). При пустом `tree`
  `calc_coverage_from_elem_index()` возвращает `"unknown"`, а не `"service"`:
  среди таких форм есть объектные `ФормаЗаписи` регистров сведений.

### form_classifier — объектные и сервисные формы

- `FormClass` + `classify_form()` + `classify_form_by_name()` +
  `classify_form_by_bindings()` — разделение форм на `object` / `service` /
  `unknown` по имени и структуре привязок (issue #98, PR #99).
- `classify_empty_tree_form()` — диагностика форм с пустым `tree`:
  возвращает пару «класс, причина». `SERVICE` только при совпадении
  с проверенным списком `SERVICE_FORM_NAME_PATTERNS`.
- `classify_no_widgets_form(form_name, reason) -> FormClass` —
  классификация форм категории `NO_TABULAR_NO_WIDGETS`: `SERVICE`
  при совпадении паттерна, `UNKNOWN` во всех защитных случаях
  (платформенные имена, пустое имя, неизвестный паттерн) (issue #109, PR #111).
- Константы: `SERVICE_FORM_NAME_PATTERNS` (16 префиксов),
  `PLATFORM_OBJECT_FORM_NAMES` (17 имён), `EMPTY_TREE_NAME_HINTS` (19).
- Верификация на УТ 10.3, 2216 форм: проиндексировано 2169 (97.9%),
  NO_TABULAR_NO_WIDGETS 17 → SERVICE 17 / UNKNOWN 0, ложных SERVICE 0,
  исключений 0. Тестов 542 (issue #109, PR #111).
- Ограничение: 47 форм в `unknown` после #107; следующие задачи: #108 (A, 4).

### Прочее
- `elem_parser.parse_elem_json()` — единственный парсер `*.elem.json` — issue #40
- `form_summary.build_form_summary()` — семантическая выжимка формы — issue #66
- `managed_forms.discover_elem_forms()` — обнаружение elem-only форм — issue #55
- `form_router` — маршрутизация запросов к формам по типу/имени

## Не реализовано / В планах
- **#107** — closed (PR #110): фикс `header[0][1]`, Pattern-ссылки `["#", UUID]`, разложение категории B на `PROGRAMMATIC_NO_DEFS` (8) и `BSL_SOURCE_MISMATCH` (11). Покрытие 97.9%.
- #108 — категория A (4 форм): `object_decoder` не распознал layout владельца
  (`ChartOfCharacteristicType`), либо владельца нет вовсе (`CommonForm`) —
  нужна отдельная ветка `NO_OWNER_OBJECT`.
- ~~#109~~ — **closed** (PR #111): категория C (17 форм) переведена из
  `UNKNOWN` → `SERVICE` через `classify_no_widgets_form()`.
  Production: 17/17 SERVICE, 0 ложных, 542 теста.
- Формы с непустой картой реквизитов и нулевой привязкой (#89) —
  диагностика и разбор причин.
- `Ref#uuid` → имя объекта метаданных (#88) — глобальный индекс UUID всех
  объектов выгрузки.
- `form_context` (#77) — материализация `FormEntry → FormContext`
  (`BSL + FormSummary + resolved bindings + to_llm_prompt_fragment`).
- CLI для `check_drift` (аналогично `scan_forms --mode`)
- Детекция дрейфа по `form_summary` (семантический уровень)
- Инкрементальный baseline (обновление только изменённых форм)

## Известные ограничения

- `elem_sha256` вычисляется только при наличии `*.elem.json`; формы без него
  не участвуют в `structure_modified` (не баг, дизайн).
- Вложенность групп в `elem_parser` не реконструируется полностью —
  хэш строится по достоверной части дерева.
- В управляемых формах отсутствие UUID может компенсироваться только точным
  структурным подтверждением через `props` или непосредственного
  родителя-таблицу. Префиксные совпадения намеренно не угадываются.
- Длина строкового реквизита и квалификаторы не декодируются: в исследованных
  layout эти данные не найдены на устойчивой позиции.
- `Synonym` заполняется не всегда — в production-layout присутствует,
  в compact-layout часто отсутствует.
- Ссылочные типы остаются в виде `Ref#<uuid>` до #88; составные типы
  (`CompositeType`) и compact-layout дают `Type=None` — 20.2% реквизитов
  на контрольной выгрузке.
- Отсутствие `data_path` у надписей, групп, страниц, разделителей и панелей
  команд — штатный результат, а не пробел декодирования.
- Три платформенных реквизита (`Код`, `Наименование`, `Родитель`) в
  управляемых формах не получают `data_path` до реализации #88 — их адрес
  зашифрован как `Ref#uuid` и пока не разрешается. Метрика `coverage_metric`
  корректно показывает их как непривязанные.
- На проверенной конфигурации остаются 47 форм без достоверно извлечённой
  разметки. Они сохраняют `elem_index_ok=False`, но после #105 каждая имеет
  явную причину (`UnindexedReason`): C 17, BSL_SOURCE_MISMATCH 11, PROGRAMMATIC_NO_DEFS 8, PLATFORM_DYNAMIC 7, A 4. Нестрогий рекурсивный
  поиск UUID намеренно не применяется — извлечение по этим категориям
  вынесено в #107 / #108 / #109.
