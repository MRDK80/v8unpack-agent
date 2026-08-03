# Changelog

Все значимые изменения фиксируются здесь.
Формат следует [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added
- `v8unpack_agent/form_classifier.py`: `FormClass` + `classify_form_by_name()` +
  `classify_form_by_bindings()` + `classify_form()` + `classify_empty_tree_form()` —
  классификация форм на объектные и сервисные. Мастера, помощники и диалоги
  привязывают поля к временным реквизитам формы, а не к реквизитам объекта
  (`Объект.*`) — это архитектурный паттерн платформы 1С, а не пробел парсера.
  Критерий двойной (OR): по имени через `SERVICE_FORM_NAME_PATTERNS`
  (16 проверенных префиксов: `помощник`, `мастер`, `диалог`, `подбор` и др.)
  и по структуре привязок. Форма без `Объект.*` среди data-элементов —
  `service`; форма вообще без data-элементов — тоже `service`
  (информационная / навигационная). Широкие префиксы вроде `форма` в этот
  список намеренно не входят: под них попадают стандартные объектные формы
  платформы (issue #98, PR #99).
- `CoverageReport.form_class: str` + параметр `form_name` в
  `calc_data_path_coverage()` — класс формы (`"object"` | `"service"` |
  `"unknown"`) сохраняется в отчёте и сериализуется в `to_dict()`.
  По умолчанию `"unknown"` — обратная совместимость для кода, не передающего
  `form_name` (issue #98, PR #99).
- `classify_empty_tree_form(form_name) -> tuple[FormClass, str]` —
  отдельная диагностика форм с пустым `tree` в `.elem.json`. Возвращает пару
  «класс, причина»: `by_service_pattern` (совпадение с проверенным списком —
  единственный случай, когда даётся `service`), `platform_object_name_unparsed`
  (стандартное имя объектной формы платформы, красный флаг для парсера),
  `empty_tree_name_hint` (имя похоже на сервисное, подтверждения нет),
  `unparsed_empty_tree`, `no_name`. Константы `PLATFORM_OBJECT_FORM_NAMES`
  (16 стандартных имён) и `EMPTY_TREE_NAME_HINTS` (issue #98, PR #99).
- `v8unpack_agent/coverage_metric.py`: `calc_data_path_coverage(elements)` +
  `CoverageReport` — двухслойная метрика покрытия `data_path`. Знаменатель
  включает только элементы данных (`DATA_ELEMENT_TYPES`: `Field`, `InputField`,
  `Table`, `CheckBox`, `Calendar`, `Chart`, `Picture`). Служебные элементы
  (`Label`, `CommandPanel`, `Panel`, `Page`, `Group`, `Button`, `Separator`)
  вынесены в именованную константу `SERVICE_ELEMENT_TYPES` и исключены из счёта.
  Стандартные реквизиты платформы (`Код`, `Наименование`, `Родитель` и др.)
  учтены в `PLATFORM_STANDARD_ATTRIBUTES`; их корректная привязка зависит от
  резолюции `Ref#uuid` — тема #88. Отчёт `CoverageReport` хранит обе метрики:
  `bound_data_elements / data_elements` и `total_elements` для справки.
  Проверено на `Catalog/Банки`: 11/14 = 78.6% (3 неразрешённых — платформенные
  реквизиты, ждут #88); `Catalog/Контрагенты`: 45/45 = 100.0% (issue #90, PR #97).
- `v8unpack_agent/object_decoder.py`: `decode_object_attributes(path)` +
  `DecodeResult` — декодирование реквизитов объекта из raw-секции `header`
  в `Catalog.json`, `Document.json` и других объектных JSON. Возвращает
  `{"Properties": [...], "TabularSections": [...]}`, где каждый реквизит
  содержит `Name`, `UUID` и `Type`. Примитивы разрешаются через таблицу
  кодов (`String`, `Number`, `Boolean`, `Date`), ссылочные типы — в вид
  `Ref#<uuid>`; приведение UUID к имени объекта метаданных вынесено в #88.
  Табличные части декодируются вместе с колонками. При неизвестном layout
  возвращается частичный результат с диагностикой, без исключений
  (issue #84, PR #87).
- `elem_parser`: консервативный структурный fallback для управляемых форм,
  когда UUID не даёт привязку. Точное совпадение имени элемента с реквизитом
  формы (включая вложенные узлы `props`) даёт путь из одного сегмента;
  колонка таблицы разрешается как `<реквизит формы>.<колонка>` только через
  непосредственный родительский сегмент ключа `data`. UUID и явно заданный
  путь имеют приоритет; префиксные эвристики намеренно не применяются
  (issue #85, PR #86).
- `elem_parser.decode_legacy_data_path()` + `is_legacy_form_data()` —
  декодирование привязки элемента к данным в **обычных** формах. Источник
  данных назван в поле `prop` записи `data`, имя реквизита — в `raw[4][1]`
  при теге `raw[4][0] == "14"`. Путь имеет вид `<prop>.<реквизит>`
  (`СправочникОбъект.Город`); если имя реквизита совпадает с `prop`,
  элемент связан с самостоятельным реквизитом формы и путь состоит из
  одного имени. Записи без `prop` привязки не имеют: тег `"14"` есть и у
  надписей, и у разделителей, поэтому признаком привязки служить не может
  (issue #85, PR #86).
- `catalog_resolver`: `resolve_data_path()` + `ResolvedBinding` + `object_json_path()` —
  best-effort резолюция `data_path` через JSON объекта (`Catalog.json` и др.);
  поддерживает пути `Объект.Реквизит` и `Объект.ТЧ.Реквизит`; при отсутствии файла
  или нераспознанном пути возвращает `resolved=False` без исключений.
  Полная резолюция на реальных выгрузках обеспечена #84
  (`decode_object_attributes`) (issue #76, PR #83).
- `FormEntry.bsl_sha256: Optional[str]` — SHA-256 содержимого `.obj.bsl` на момент
  сканирования; используется как основной критерий детекции изменённого кода формы
  в `check_drift()` (issue #38).
- `FormEntry.elem_sha256: Optional[str]` — SHA-256 нормализованного дерева элементов
  формы (`form_elements_index`); используется как независимый критерий детекции
  изменения разметки формы (`structure_modified`) в `check_drift()` (issue #40).
- `DriftReport.structure_modified: list[str]` — новая категория отчёта: ключи форм,
  у которых изменилась структура элементов (дерево `elem.json`) при неизменном BSL
  (issue #40). Учитывается в `has_drift`.
- `FormEntry.bsl_mtime: float` — поле mtime файла `.obj.bsl` на момент сканирования;
  сохраняется как диагностическое поле и legacy fallback для старых индексов без
  `bsl_sha256`.
- `FormScanIndex.load()` — загрузка индекса из JSON с обратной совместимостью:
  старые записи без `bsl_sha256` / `elem_sha256` получают `None`; поведение
  `check_drift()` для таких записей документировано (legacy fallback через
  `bsl_mtime` / тихий пропуск `structure_modified`).
- `DriftReport.modified` теперь **работает**: возвращает ключи форм, чей `.obj.bsl`
  изменился после записи baseline в `FormScanIndex` (issue #18).
- scan_forms: режим `--mode external` для распакованных внешних обработок
  (`External/<имя>/Form/<форма>/Form.obj`); поле `form_elem_path` в FormEntry (#25).
- scan_forms external: поддержка контейнера `ReportForm/` для внешних отчётов;
  `object_type="ExternalReport"` определяется по контейнеру `ReportForm` (#32).
- `v8unpack_agent/managed_form_summary.py`: `build_managed_form_summary(form_dir)`
  + `build_managed_form_summary_from_elem_index(result)` + `to_normalized_json()` —
  детерминированная семантическая выжимка формы (attributes / commands / elements /
  events / relations) поверх канонического `parse_elem_json`. Отдельный слой-адаптер
  реального формата не вводится: `parse_elem_json` — единственный парсер
  `*.elem.json` (issue #66, PR #68).
- **elem-only формы** (`*.elem.json` без `.obj.bsl`) добавлены в `FormScanIndex`
  через `_collect_elem_only_forms` + `discover_elem_forms` (issue #57 / #55).
  Поле `FormEntry.elem_json_path` — relative-to-root путь к `*.elem.json`.
  Подтверждено на 49 формах реальной конфигурации (45 с `elem_sha256`, 4 пустых).
- **`drift_checker._index_snapshot`** теперь возвращает четвёртый элемент —
  `elem_only_keys: set[str]`: ключи форм с `elem_json_path` и несуществующим
  `bsl_path` (elem-only по дизайну). Используется в `_stale_keys` и логике
  `added/removed` (issue #58).
- **`check_drift(mode=...)`** — новый параметр `mode: Literal["config", "external"]`
  (default `"config"`). При `mode="external"` корректно сканирует external-layout
  через делегирование в `scan_forms(mode=mode)`. Устраняет ложный дрейф всех
  внешних форм сразу после создания baseline (issue #73).

### Changed
- `calc_coverage_from_elem_index()`: при `elem_index_ok=False` или пустом
  списке элементов возвращает `form_class="unknown"`, а не `"service"`.
  Промежуточный вариант трактовал пустой `tree` как «безопасный дефолт:
  форма без элементов не может быть объектной». Верификация на 2231 форме
  опровергла это: среди таких форм три `ФормаЗаписи` регистров сведений
  и семь основных форм `Форма` отчётов и обработок — гарантированно
  объектные, чью разметку `elem_parser` не извлёк. Отнесение их к `service`
  исключило бы объектные формы из агрегированного покрытия и замаскировало
  пробел в парсере (issue #98, PR #99).
- `coverage_metric`: импорт `form_classifier` выполняется лениво внутри функций
  (`calc_data_path_coverage`, `calc_coverage_from_elem_index`) — `form_classifier`
  импортирует `DATA_ELEMENT_TYPES` на уровне модуля, и импорт на верхнем уровне
  дал бы цикл (issue #98, PR #99).
- `elem_parser.load_owner_attribute_map()` строится из результата
  `object_decoder.decode_object_attributes`; независимая рекурсивная реализация
  обхода `header` удалена. Прежний обход собирал UUID из произвольных узлов и
  давал мусорные карты — на четырёх legacy-формах
  `DataProcessor/ВиртуальнаяАгрегацияУпаковокИСМП` карта из одной записи
  никогда не использовалась, поскольку обычные формы декодируются через `prop`.
  Число разрешённых `data_path` на production-выгрузке не изменилось:
  14134 до и после (issue #84, PR #87).
- `elem_parser`: отсутствие UUID-кандидата в управляемой форме больше не
  означает автоматическую потерю `data_path`: после UUID-декодирования
  применяется только доказуемый структурный fallback. Имена реквизитов
  собираются рекурсивно из `props`; неоднозначные `unique_prop_prefix` и
  `longest_prop_prefix` остаются неразрешёнными (issue #85, PR #86).
- `elem_parser`: разбор привязки по UUID применяется только к формам без
  `prop` (управляемым). В обычных формах UUID внутри `raw` описывают класс
  виджета, а не привязку к данным, и попытка их разрешить давала ложные
  срабатывания (issue #85).
- `elem_parser`: одноимённые записи из секций `data` и `props` схлопываются
  в одну (`_merge_source_duplicates`), приоритет у `data` — она несёт
  `path`, `page` и `data_path`. Недостающие поля добираются из проигравшей
  записи, список слитых источников сохраняется в `merged_sources`.
  Реквизиты формы без одноимённого элемента (`СправочникОбъект`) остаются
  отдельными записями; элементы с разными непустыми `path` не сливаются
  (issue #85).
- `check_drift()`: при наличии `bsl_sha256` в baseline-индексе использует hash
  как основной критерий `modified`; при отсутствии — legacy fallback через
  `bsl_mtime` (issue #38).
- `check_drift()`: при наличии `elem_sha256` в baseline-индексе заполняет
  `structure_modified`; при отсутствии (старый индекс) — тихо пропускает
  без false-positive (issue #40).
- `FormScanIndex.to_dict()` сериализует `bsl_mtime`, `bsl_sha256`, `elem_sha256`
  в JSON.
- `drift_checker._index_snapshot()` читает `bsl_mtime` из JSON-записи индекса
  вместо прямого обращения к диску через `Path(bsl).stat().st_mtime`.
- scan_forms external: bsl-файл формы ищется по кандидатам `<Container>.obj.bsl`
  (v8unpack 1.2.11) → `<Container>.obj` (legacy), приоритет у `.bsl`; типизация
  для контейнера `Form/` — по имени модуля объекта с fallback
  `ExternalDataProcessor` (#32).
- **`check_drift()` — `keys_with_baseline_elem`** теперь строится по всем ключам
  `index_elem` с непустым хэшем (не только по пересечению с `disk_keys`). Это
  позволяет elem-only формам участвовать в `structure_modified` (issue #58).
- **`check_drift()` — пересканирование для `structure_modified`** теперь вызывает
  `scan_forms(root, include_elem_only=True)` чтобы elem-only формы попали в
  `current_elem_map` (issue #58).
- **`check_drift()` — `added/removed/modified`** вычисляются по `index_keys_bsl =
  index_keys - elem_only_keys`, исключая elem-only из BSL-based логики (issue #58).
- **`_disk_snapshot()`** — убран собственный hard-coded обход 4- и 3-уровневого
  layout; обход делегирован в `scan_forms(mode=mode, include_elem_only=False)`.
  Параметр `mode` пробрасывается из `check_drift()` (issue #73).

### Fixed
- **Сервисные формы искажали агрегированную метрику покрытия `data_path`.**
  Мастера и помощники давали 0% по объектному критерию, хотя их поля привязаны
  к реквизитам формы корректно. Теперь покрытие считается по 1957 формам
  с реально распарсенной разметкой (164 `object` + 1793 `service`), формы без
  разметки в знаменатель не входят. Верификация на 2231 директории:
  0 ошибок, 0 исключений, баланс сходится (issue #98, PR #99).
- **Вложенные реквизиты формы не участвовали в структурной резолюции.**
  Рекурсивный обход `props` теперь учитывает узлы `child` и другие дочерние
  контейнеры. На контрольной реальной выгрузке из 4549 исходно неразрешённых
  Field-записей безопасно восстановлены 3292, а 1257 оставлены без догадок;
  это результат аудита конкретной выгрузки, не универсальная метрика
  покрытия (issue #85, PR #86).
- **Элементы обычных форм не получали `data_path`.** Разбор опирался
  исключительно на UUID из `raw`, тогда как обычные формы используют
  собственный механизм привязки через `prop`. Проверено на
  `Catalog/Банки`: `ФормаЭлемента` — 8 полей с привязкой (было 0),
  `ФормаСписка` — 4 (было 0). Стандартные реквизиты `Код`,
  `Наименование`, `Родитель` в обычных формах разрешаются автоматически,
  поскольку привязка идёт по имени, а не по UUID (issue #85, PR #86).
- **Ложное предупреждение «неоднозначная привязка» на форме списка.**
  Возникало при попытке разрешить UUID виджета через карту реквизитов
  объекта. Устранено отключением UUID-ветки для обычных форм (issue #85).
- **Дубли элементов по имени в `form_elements_index`.** В формах списка
  имя реквизита формы совпадает с именем элемента, из-за чего запись
  попадала в индекс дважды — из `data` с привязкой и из `props` без неё.
  Поиск элемента по имени через `next()` мог вернуть запись без
  `data_path`. `ФормаСписка`: 11 элементов → 7 (issue #85).
- `check_drift()` / `modified`: повторная полная распаковка неизменённого `.cf`
  больше не даёт ложные `modified` — детекция переведена с `bsl_mtime` на
  `bsl_sha256` (issue #38).
- `DriftReport.modified` ранее всегда возвращал `[]` — baseline отсутствовал
  в `FormScanIndex`. Исправлено добавлением поля `bsl_mtime` (issue #18).
- scan_forms external: формы с суффиксом `.bsl` (`Form.obj.bsl`, v8unpack 1.2.11)
  не находились — режим искал только `Form.obj` без суффикса, возвращая 0 форм.
  Проверено на реальной выгрузке (13 форм: обработки + отчёты) (issue #32).
- **`_stale_keys()`: elem-only формы больше не попадают в `stale_extractions`.**
  Отсутствие `bsl_path` у elem-only форм — это дизайн, а не признак устаревшей
  экстракции. Исправлено пропуском ключей из `elem_only_keys` (issue #58).
  Проверено: 49 elem-only форм реальной конфигурации УТ 10.3 — ложный дрейф
  устранён.
- **`structure_modified` для elem-only форм**: пересечение с `disk_keys` (только
  BSL-формы) давало пустое множество — elem-only формы никогда не проверялись.
  Исправлено: кандидаты берутся напрямую из `index_elem` (issue #58).
- **`check_drift(mode="external")`: ложный `removed` для всех внешних форм
  сразу после создания baseline.** `_disk_snapshot` не имел параметра `mode`
  и возвращал 0 форм при external-layout — `removed = index_keys - {} = все`.
  Исправлено делегированием обхода в `scan_forms(mode=mode)` (issue #73).
  Проверено на реальных данных: 14 форм (ExternalDataProcessor + ExternalReport),
  `has_drift=False` сразу после baseline, изменение BSL корректно детектируется.
- **`check_drift()`: ложный дрейф при передаче файла вместо директории (issue #91).**
  Если `cf_export_root` указывал на существующий файл (например, случайно передавался
  сам `forms_index.json`), `_disk_snapshot` возвращал пустой `{}`, весь baseline
  попадал в `removed`, `has_drift=True` — полностью ложный отчёт без каких-либо
  диагностических сообщений. Исправлено: `check_drift()` теперь немедленно бросает
  `NotADirectoryError` с внятным сообщением, если `cf_export_root` не является
  существующей директорией (несуществующий путь или файл). Дополнительно: если скан
  валидной директории возвращает ноль форм при непустом baseline — выдаётся
  `logger.warning` (ранее тихий сценарий).