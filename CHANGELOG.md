# Changelog

Все значимые изменения фиксируются здесь.
Формат следует [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added

- **`docs/research/ref_resolver_issue143.md` + `examples/unresolved_refs_report.py`** —
  отчёт и инструмент воспроизведения исследования #143 (PR #173, squash-мерж
  `f8cd86f`). Остаток неразрешённых `Ref#uuid` после #147 классифицирован
  полностью: 48 UUID / 1301 вхождение — `definition_known` 28 (158 вхождений,
  12.14%), `reference_only` 20 (1143, 87.86%), `definition_unknown_layout` 0,
  `ambiguous` 0. Решение по всем классам — keep unresolved, прирост coverage
  0 п.п. RCA: аномалий индекса нет; 10 UUID — нессылочные типовые грани уже
  проиндексированных объектов, 18 UUID — виды метаданных вне
  `REFERENCE_TYPE_PREFIXES`. Production-код не изменён (issue #143).
- **`examples/missing_object_attributes_report.py`** — research-инструмент
  классификации форм без `FormContext.object_attributes` (issue #163).
  Разделяет две точки отказа (`object_json_path() is None` против
  `DecodeResult.ok is False`), раскладывает отказы по `DecodeError`, определяет
  структурную роль найденного JSON (`owner_object_file`, `export_root_neighbour`,
  `form_artifact`, `type_container_neighbour`, `unrelated_neighbour`) и назначает
  класс причины только по структурным признакам, без эвристик по имени формы.
  Режимы `--runs N` (подпись агрегата sha256/16, расхождение → код возврата 1) и
  `--controls` (контроли A/B/C). Вывод обезличен: количества, коды отказа, типы и
  нормализованные ключи путей вида `<root>/<L1>/<candidate>.json`; `--local-names`
  и `--csv` предназначены только для локального запуска. Production-код не изменён.
- **`docs/research/missing_object_attributes_issue163.md`** — отчёт исследования
  #163. На боевой выгрузке 2216 форм: 2054 с `object_attributes`, 162 без (7.3%).
  Все 162 — `CommonForm`; точка отказа `decode_error:header_missing` 162/162,
  `object_json_path() is None` 0, `DecodeResult.ok is False` 162. Уровень
  `ObjectName` отсутствует у 162/162, а `object_json_path()` резолвит все 162
  формы в один и тот же файл корня выгрузки (`distinct candidates = 1`, роль
  `export_root_neighbour`), поэтому найденный JSON не является объектом-владельцем
  и `HEADER_MISSING` не относится к owner-layout. Итог: `no_owner_object` 162
  (100%), решение `keep as is`; `type_out_of_scope`, `layout_unsupported`,
  `path_convention_miss`, `broken_json`, `insufficient_evidence` — по 0. Случаев
  #160: 0, случаев #151: 0. Разрез `form_classifier`: `service` 158 (97.5%),
  `unknown` 4 (2.5%), `object` 0; все 4 `unknown` — формы с пустой выжимкой
  (категория #98 / #105). Контроли: A — форма с владельцем даёт `decode.ok=True` и
  роль `owner_object_file`; B — общая форма даёт `object_name=absent` и класс
  `no_owner_object`; C — копия реального owner JSON без ключа `header` даёт
  `header_missing`, роль `owner_object_file` и класс `layout_unsupported`.
  Детерминированность: два прогона, подпись агрегата `788bd54b0fe931d0`.
  Полный `pytest` — 857 passed (issue #163).
- **Конвенция `docs/research/`** — отчёты разовых исследований по данным вынесены
  из тематических `docs/<module>.md` в отдельный каталог
  `docs/research/<тема>_issue<N>.md` и получают строку в таблице «Документация»
  корневого `README.md`. Первым отчётом в каталоге стал
  `ref_resolver_issue143.md` (#143, PR #173). Ранее такие результаты сводились
  в `docs/elem_parser.md`,
  `docs/form_classifier.md` и `docs/IMPLEMENTATION_STATUS.md` (issue #163).
- `examples/README.md` — классификация примеров по требованиям к входным
  данным: восемь самодостаточных (запускаются без аргументов на синтетике,
  годятся для регрессионного прогона) и четыре требующих реальной выгрузки
  (`extract_skd_queries.py`, `legacy_list_form_bindings.py`,
  `unresolved_refs_report.py`, `missing_object_attributes_report.py`). Зафиксировано,
  что формулировка «проверены все файлы `examples/`» без явной оговорки
  относится только к первой группе.

- `tests/test_init_lazy_imports_issue134.py` — 11 тестов в чистом subprocess:
  после `import v8unpack_agent` в `sys.modules` нет `forms_index` и
  `skd_extractor`; каждая группа грузит только свой подмодуль и не подтягивает
  чужой; повторный доступ возвращает тот же объект; корневые и прямые импорты
  дают идентичные объекты; `__all__`, прежние ленивые группы и `AttributeError`
  для неизвестного имени сохранены (issue #134, часть B).
- `v8unpack_agent.drift_checker.form_key` — публичное имя составного ключа формы
  `object_type/object_name/container_name/form_name` с содержательным docstring.
  `_form_key` остаётся тонким алиасом той же функции (`_form_key is form_key`),
  формат ключа и разделитель `/` не изменились (issue #134, PR #136).
- `tests/test_form_key_public_issue134.py` — 12 characterization/regression тестов:
  наличие docstring, идентичность алиаса, сигнатура, разделитель, побайтовое
  совпадение ключей с baseline на `main`, отпечаток набора кейсов, отложенный
  импорт в `FormRouter.reindex` и отсутствие `form_key` в корневом `__all__`.
  Полный прогон 793 → 805 passed (issue #134, PR #136).
- `tests/test_init_lazy_imports_issue131.py` — 14 тестов в чистом subprocess: после `import v8unpack_agent` в `sys.modules` нет `form_artifact`, `drift_checker`, `logging`, `hashlib` и `datetime`; подмодуль грузится по первому обращению к символу и возвращает тот же объект при повторном; корневой и прямой импорт дают идентичные объекты; `__all__`, существующие ленивые группы и `AttributeError` для неизвестного имени сохранены (issue #131, PR #132).
- **`v8unpack_agent.__all__` и ленивый `__getattr__`** — `FormContext`,
  `build_form_context` и `to_llm_prompt_fragment` экспортируются из корня
  пакета по образцу группы `FormSummary`. Загрузка остаётся ленивой:
  `import v8unpack_agent` не импортирует `form_context`, модуль
  подгружается при первом обращении к символу. Прямой импорт
  `from v8unpack_agent.form_context import ...` продолжает работать
  (issue #124).
- **`form_context.FormContext`** — frozen-датакласс с материализованным
  содержимым формы: `form_name`, `container_name`, `object_type`,
  `object_name`, `bsl_text`, `summary`, `metadata`. `FormEntry` остаётся
  карточкой указателей, `FormContext` открывает то, на что она указывает
  (issue #77).
- **`form_context.build_form_context(form_entry, unpacked_root)`** — читает
  `bsl_path` явно как UTF-8, строит `FormSummary` единственным существующим
  парсером и отбирает компактные `metadata`. Отсутствие BSL — штатный
  `None`, пустой файл модуля — `""`; отсутствие `*.elem.json` даёт пустые
  бакеты и `warnings` парсера. Второй путь разбора не вводится, привязки
  `data_path` не создаются (issue #77).
- **`form_context.to_llm_prompt_fragment(context, max_chars=-1)`** —
  детерминированный фрагмент `# FORM` → `## SUMMARY` → `## BSL`. Обрезка
  по умолчанию возвращает полный контекст без обрезки. При положительном
  лимите обрезка выполняется последним шагом, поэтому
  `len(result) <= max_chars`; `0` и значения меньше `-1` дают пустую строку
  без исключения (issue #77).
- **`tests/test_form_context.py`** — 37 тестов: BSL + elem, elem-only, форма
  без `elem.json`, отсутствующий каталог формы, отбор `metadata` без
  дублирования `FormEntry`, чтение UTF-8 с кириллицей, детерминизм, порядок
  summary раньше BSL, границы лимита `0` / отрицательного / меньше
  заголовков. Все фикстуры синтетические. Всего тестов 708 → 745
  (issue #77).
- **`examples/form_context.py`** и **`docs/form_context.md`** — синтетический
  запускаемый пример и документация: фактический API, поведение без
  BSL/elem, truncation contract и ограничения (issue #77).

- **`chain_data_path.ZeroBindingReason`** — машиночитаемая причина отсутствия
  `data_path`: `no_bind_slot`, `bind_slot_unbound_marker`,
  `bind_slot_not_a_chain`, `chain_malformed`, `chain_too_short`,
  `chain_table_not_declared`, `chain_segment_unresolved`,
  `chain_name_mismatch`, `mixed`. Общего статуса вроде `unknown` в наборе нет:
  отдельный диагностический код полезнее молчания и полезнее частичного
  «успеха» (issue #116, PR #120).
- **`chain_data_path.classify_element_zero_binding(block, segment_tables,
  form_attribute_ids, element_name=None)`** — причина по блоку `raw[11]`.
  Возвращает `None`, если цепочка разбирается и даёт подтверждённый
  `data_path`. Порядок проверок совпадает с порядком в
  `decode_chain_data_path`, поэтому первый структурный отказ важнее
  последующего сравнения имён (issue #116, PR #120).
- **`chain_data_path.classify_raw_zero_binding(...)`** — причина по целой
  записи элемента; отсутствие слота `BIND_SLOT` — самостоятельная причина,
  а не повреждённая цепочка (issue #116, PR #120).
- **`chain_data_path.aggregate_form_zero_binding(reasons)`** — причина уровня
  формы: единственная причина возвращается как есть, несколько разных дают
  `MIXED`, пустой набор — `None`. Результат не зависит от порядка элементов
  (issue #116, PR #120).
- **`tests/test_chain_zero_binding_issue116.py`** — 83 теста на девять
  категорий и регрессии: ни одной выдуманной привязки, подтверждённый путь
  #89 не меняется, входные структуры не мутируются, результат детерминирован.
  Все фикстуры синтетические. Всего тестов 675 → 708 (issue #116, PR #120).
- **`object_decoder.decode_object_attributes(object_json, type_resolver=None)`** —
  опциональный резолвер ссылочных типов. `type_resolver` получает UUID **без**
  префикса `Ref#` и возвращает читаемое имя объекта метаданных
  (`CatalogRef.Города`) либо `None`. Замена применяется к реквизитам объекта и
  к колонкам табличных частей; примитивные и уже распознанные значения
  резолверу не передаются. Отсутствие резолвера, ответ `None`, пустая строка
  или исключение внутри резолвера сохраняют безопасный fallback `Ref#<uuid>`;
  сбой пишется в `warnings` как `REF_RESOLVER_FAILED` и не прерывает
  декодирование. Существующий вызов `decode_object_attributes(path)` работает
  без изменений, сериализованный результат идентичен прежнему
  (issue #88, PR #118).
- **`FormScanIndex.reference_types: dict[str, str]`** +
  **`FormScanIndex.resolve_reference_type(uuid) -> str | None`** — глобальный
  индекс `uuid → имя ссылочного типа`. Собирается в `scan_forms` внутри уже
  существующего обхода конфигурации (`_scan_config`), второй обход дерева и
  параллельный discovery не вводятся. Подпись `resolve_reference_type`
  совместима с параметром `type_resolver`, поэтому индекс передаётся в декодер
  напрямую. UUID берутся из блока идентификации объекта `header[0][1]`
  (слоты 1–3): у объекта метаданных несколько идентификаторов, и ссылка
  реквизита адресует не обязательно слот 2. Первая запись по UUID сохраняется,
  конфликт разных объектов и неполные метаданные попадают в `scan_warnings`
  (issue #88, PR #118).
- **`scan_forms.REFERENCE_TYPE_PREFIXES`** — единая таблица соответствия вида
  метаданных и префикса ссылки: `Catalog` → `CatalogRef`, `Document` →
  `DocumentRef`, `Enum` → `EnumRef`, `ChartOfCharacteristicType` →
  `ChartOfCharacteristicTypeRef`, `ExchangePlan` → `ExchangePlanRef`,
  `BusinessProcess` → `BusinessProcessRef`, `Task` → `TaskRef`. Регистры не
  включены: ссылочного типа у них нет (issue #88, PR #118).
- **`tests/test_reference_type_resolution.py`** — 13 синтетических тестов:
  резолюция известной ссылки и передача резолверу UUID без префикса, `None`
  сохраняет `Ref#uuid`, отсутствие резолвера не меняет прежний результат,
  примитивный тип резолверу не передаётся, несколько ссылок резолвятся
  независимо, индекс различает `Catalog` и `Document` без второго discovery,
  детерминизм и предупреждение при дубликате UUID, безопасный fallback и
  диагностика при неполных метаданных, резолюция из слотов `header[0][1][1]` и
  `header[0][1][3]`, единое имя типа для всех идентификаторов одного объекта,
  `EnumRef` для перечисления, отсутствие индексации для вида без ссылочного
  типа (issue #88, PR #118).
- **`v8unpack_agent/chain_data_path.py`** — декодирование сегментных цепочек
  `data_path` в управляемых формах. Публичный API:
  `build_form_attribute_ids()`, `build_form_segment_tables()`,
  `load_form_segment_tables()`, `decode_chain_data_path()`,
  `enrich_elements_with_chain_paths()`. Блок привязки лежит в `raw[11]`
  и имеет вид `[<счётчик>, <сегмент>, ["0", <uuid типа>], <сегмент>, ...]`:
  счётчик равен числу элементов после него, записи `["0", <uuid>]` описывают
  тип звена и в пути не участвуют, `[<id>]` разрешается по дереву реквизитов
  формы (`props`, включая `child`), `[<id>, <uuid таблицы>]` — по таблицам
  определений из большого JSON формы (`$.form[0][0][3]`).
  Привязка принимается только при выполнении двух независимых условий:
  UUID таблицы объявлен в адресах определений этой формы и склейка имён
  сегментов посимвольно равна имени элемента (issue #89, PR #115).
- **`tests/test_elem_parser_issue89.py`** — 15 тестов на синтетическом
  обезличенном fixture: полная цепочка через таблицы определений, короткая
  цепочка по `props`, сборка таблиц и дерева идентификаторов, а также
  отрицательные случаи — несовпадение склейки с именем, id вне таблицы,
  чужой UUID таблицы, оборванный счётчик, блок старого формата со ссылкой
  на реквизит объекта, пустые таблицы определений, неизменность входных
  данных и файла `elem.json` (issue #89, PR #115).
- **`form_classifier.classify_no_widgets_form(form_name, reason) -> FormClass`** —
  новая точка входа для форм с `UnindexedReason.NO_TABULAR_NO_WIDGETS`.
  Возвращает `FormClass.SERVICE`, когда имя совпадает с проверенным паттерном
  или `classify_empty_tree_form` возвращает `by_service_pattern` /
  `empty_tree_name_hint`; иначе `UNKNOWN`. Ни одна форма с
  `platform_object_name_unparsed` не получает `SERVICE`.
  17 форм live-базы переведены из `UNKNOWN` → `SERVICE` (issue #109, PR #111).
- **`tests/test_form_classifier_issue109.py`** — 37 тестов (TDD RED→GREEN):
  17 параметризованных сервисных форм, граничные случаи (`ФормаЗаписи`,
  `Форма`, пустое имя, неизвестный паттерн), детерминизм, иммутабельность,
  регрессии `classify_form` и `classify_empty_tree_form` (issue #109, PR #111).
- **`UnindexedReason.TABULAR_FIELD_PROGRAMMATIC_NO_DEFS`** — программная
  `ТаблицаЗначений`/`ДеревоЗначений`, колонки не объявлены нигде: ни UUID
  в TabularField, ни `Колонки.Добавить` в модуле формы. 8 форм
  (issue #107, PR #110).
- **`UnindexedReason.TABULAR_FIELD_BSL_SOURCE_MISMATCH`** — в BSL есть
  объявления колонок, но у другого источника (`ВыбранныеСтроки` vs
  `ТабличноеПоле`). Сопоставление по имени дало бы фантомные колонки.
  11 форм (issue #107, PR #110).
- **`tests/test_elem_parser_issue103.py::test_pattern_hash_reference_fallback`**
  — регрессионный тест на Pattern-ссылки. Всего 505 тестов.
- `elem_parser.UnindexedReason` (Enum) + `elem_parser.UnindexedResult` (dataclass) —
  диагностическая классификация форм, оставшихся с `elem_index_ok=False` после
  fallback #100 и #103. Значения: `TABULAR_FIELD_EMPTY_ATTR_MAP` (A),
  `TABULAR_FIELD_NO_UUID_HITS` (B), `NO_TABULAR_NO_WIDGETS` (C),
  `NO_LEGACY_JSON` (D), `UNKNOWN` (issue #105, PR #106).
- `elem_parser.classify_unindexed_form(form_root, elem_result) -> UnindexedResult` —
  определяет причину, по которой форма не проиндексирована, и возвращает
  человекочитаемый `detail`. Функция строго диагностическая: она **не создаёт
  `data_path`**, не добавляет элементов и не мутирует переданный `ElemIndexResult`.
  Внешняя обёртка перехватывает любые исключения (best-effort), внутренняя
  `_classify_unindexed_form_impl()` содержит логику приоритетов:
  нет legacy `*.json` → **D**; нет `TabularField` → **C**;
  `load_owner_attribute_map()` пуста → **A**;
  `_tabular_field_attribute_slots()` пуст → **B**; слоты есть, но форма
  не проиндексирована → **UNKNOWN** (issue #105, PR #106).
- `tests/test_elem_parser_issue105.py` — 17 тестов в пяти классах
  (`TestCategoryA`, `TestCategoryB`, `TestCategoryC`, `TestCategoryD`,
  `TestInvariants`). Фикстура категории B использует
  `_write_catalog_json_with_header()` — валидный production-layout с секцией
  `header`, который `object_decoder` успешно декодирует: карта реквизитов
  непустая, но UUID колонок в неё не попадают. Инварианты: отсутствие
  исключений на несуществующей директории, неизменность `elem_result`,
  полнота покрытия enum (issue #105, PR #106).
- `elem_parser.extract_legacy_list_form_elements()` — fallback-чтение
  `ФормаСписка` / `ФормаВыбора` с пустым `tree` и виджетом `TabularField`
  из отдельного legacy `*.json`. Поддержаны два подтверждённых формата
  привязок колонок: упорядоченные слоты вложенного блока `"20"` и точные
  ссылки `["0", UUID]`. UUID разрешаются только через карту реквизитов
  объекта-владельца, построенную `load_owner_attribute_map()` поверх
  `object_decoder`; имя источника данных извлекается из привязки
  `TabularField`. Порядок колонок сохраняется, дубликаты удаляются,
  неоднозначные слоты пропускаются без перехода к менее строгому fallback
  (issue #103).
- `elem_parser.extract_legacy_form_elements()` + `_find_legacy_form_json()` —
  fallback-чтение обычных форм с пустым `tree` в `.elem.json`. Когда `tree`
  пуст, ищется отдельный `form/*.json` рядом с `*.elem.json` (ФормаЗаписи,
  ФормаЭлемента, ФормаОбъекта, формы обработок и отчётов). Имена реквизитов
  и тип виджета извлекаются по тегу `"14"` в узлах `form[0][0][2]` через
  тот же механизм, что `decode_legacy_data_path`. Подняло **49 форм**
  из статуса `ERROR` на реальной конфигурации УТ 10.3 без регресса по
  ранее работавшим формам. Статистика после PR:
  На этом этапе: OK 1942 / FALLBACK 49 / ERROR 225; последующая поддержка
  TabularField реализована в #103.
  (issue #100, PR #102).
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
  (17 стандартных имён, добавлено `ФормаОбъекта`) и `EMPTY_TREE_NAME_HINTS`
  (issue #98, PR #99; обновлено в PR #102).
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

### Changed
- `examples/README.md` — `missing_object_attributes_report.py` добавлен в группу
  «Требующие реальной выгрузки» с обязательным аргументом `EXPORT_ROOT`;
  формулировка «эти два файла не входят в автоматический прогон» исправлена на
  «эти файлы» — в группе стало четыре примера. Добавлен подраздел с режимами запуска
  и правилом обезличенности вывода. Счётчик группы в записи о `examples/README.md`
  выше приведён к четырём, чтобы внутри одного релиза не расходились разные числа
  (issue #163).
- `README.md` — в таблицу «Документация» добавлена строка на
  `docs/research/missing_object_attributes_issue163.md`, в список примеров —
  `examples/missing_object_attributes_report.py` (issue #163).
- `v8unpack_agent.__init__` больше не импортирует `forms_index` и
  `skd_extractor` на уровне модуля: `FormsIndex`, `FormsIndexEntry`,
  `is_form_stale` и `SkdResult`, `SkdBatchResult`, `extract_skd_queries`,
  `extract_all_skd_queries` отдаются двумя независимыми ленивыми группами
  `__getattr__` с кэшированием через `globals().update(...)`. Состав `__all__`
  и все прежние способы импорта сохранены (issue #134, часть B).
- Холодный импорт пакета: модулей `v8unpack_agent.*` в `sys.modules` после
  чистого импорта 7 → 5, медиана 11 965,3 → 10 686,6 µs (−10,7 %) на 9 прогонах
  в отдельных процессах, Python 3.12.3. `json`, `dataclasses` и `typing`
  остаются загруженными: их тянут `form_router`, `form_classifier` и
  `coverage_metric` (issue #134, часть B).
- `FormRouter.reindex` использует публичное `drift_checker.form_key` вместо
  приватного `_form_key`; отложенный импорт внутри метода сохранён. Поведение
  `reindex`, формат составного ключа и публичный API не изменились
  (issue #134, PR #136).
- `v8unpack_agent.__init__` больше не импортирует `form_artifact` и `drift_checker` на уровне модуля: `FormArtifact`, `check_drift` и `DriftReport` отдаются ленивыми группами `__getattr__` с кэшированием через `globals().update(...)`. Состав `__all__` и все прежние способы импорта сохранены (issue #131, PR #132).
- `form_router` больше не импортирует `drift_checker._form_key` в шапке модуля: импорт перенесён внутрь `FormRouter.reindex`, единственного потребителя хелпера. Публичный API `FormRouter` / `RouteResult` и поведение `reindex` не изменились (issue #131, PR #132).
- Холодный импорт пакета: модулей `v8unpack_agent.*` в `sys.modules` после чистого импорта 9 → 7, `logging` / `hashlib` / `datetime` больше не загружаются, медиана 16 801,2 → 10 964,6 µs (−34,7 %) на 9 прогонах в отдельных процессах, Python 3.12.3 (issue #131, PR #132).
- Проверка структуры блока привязки вынесена из `_chain_segments` в
  `_bind_block_items`; `_chain_segments` переиспользует её без изменения
  поведения. Параллельного декодера не появилось: классификаторы опираются на
  те же `_chain_segments`, `_segment_parts`, `_known_table_uuids` и
  `_address_key` (issue #116, PR #120).
- 42 формы с непустой картой реквизитов и нулевой привязкой больше не молчат.
  Распределение по формам: `no_bind_slot` 18, `bind_slot_not_a_chain` 12,
  `mixed` 10, `chain_too_short` 2. По элементам: `bind_slot_not_a_chain` 164,
  `no_bind_slot` 62, `chain_too_short` 30, `chain_name_mismatch` 1. Форм без
  конкретной причины 0, элементов без ярлыка 0. Подтверждённых `data_path`
  13729 — без изменений относительно `main`, прежние пути не тронуты,
  конфликтов и ложных сопоставлений нет; повторный прогон детерминирован
  (issue #116, PR #120).
- Первоначальная категория `chain_malformed` на реальных данных описывала не
  ту ситуацию: все 164 элемента имели в `raw[11]` скаляр вместо списка, а
  расхождения счётчика с составом блока не встретилось ни разу. Скаляр выделен
  в `bind_slot_not_a_chain`; `chain_malformed` остаётся за рассогласованным
  списком и на этой выгрузке даёт ноль срабатываний (issue #116, PR #120).
- `FormScanIndex.to_dict()` сериализует `reference_types`;
  `FormScanIndex.load()` восстанавливает его с backward-compat: в старых
  индексах без этого поля возвращается `{}`. Прочие поля индекса и формат
  записей форм не менялись (issue #88, PR #118).
- Ссылочный тип реквизита больше не остаётся `Ref#<uuid>` по умолчанию, если
  вызывающий код передал `type_resolver`. На контрольной выгрузке
  (15717 реквизитов) ссылочных `Ref#uuid` стало 5226 → 556, разрешено в
  читаемые имена 4670: `CatalogRef` 3197, `EnumRef` 843, `DocumentRef` 596,
  `ChartOfCharacteristicTypeRef` 25, `ExchangePlanRef` 9. Размер индекса —
  2230 записей. Изменённых нессылочных записей 0, изменённых `data_path` 0,
  предупреждений 0, исключений 0, потерянных записей 0; повторный прогон дал
  идентичный результат (issue #88, PR #118).
- Метрики #84 и #88 считаны разными версиями инструмента (15888 против 15717
  реквизитов) и не смешиваются. Число 4549, упоминавшееся в постановке #88,
  относится к другой метрике — неразрешённым Field-записям `data_path` из
  аудита #85 (issue #88, PR #118).
- **Категория B обнулена.** `TABULAR_FIELD_NO_UUID_HITS` больше не
  возвращается: 26 форм проиндексированы, 19 получили точные резоны.
  Покрытие live-базы 2169/2216 = 97.9% (было 2139/2216 = 96.5%).
  Распределение: OK 2169, C 17, BSL_SOURCE_MISMATCH 11,
  PROGRAMMATIC_NO_DEFS 8, PLATFORM_DYNAMIC 7, A 4, B 0, D 0
  (issue #107, PR #110).
- Формулировка «оставшиеся 77 форм — предмет исследования #105» заменена
  результатом: причины классифицированы, дальнейшая работа разделена на
  #107 (категория B, 48 форм), #108 (категория A, 12 форм) и
  #109 (категория C, 17 форм) (issue #105, PR #106).
- `elem_parser.parse_elem_json()`: при пустом `tree` после обычного legacy
  fallback дополнительно разбирает `TabularField` через
  `extract_legacy_list_form_elements()`. Успешно извлечённые колонки получают
  `type="TabularFieldColumn"`, `source="legacy_list_form_json"` и
  `data_path="<Источник>.<Реквизит>"` (issue #103).
- `elem_parser.parse_elem_json()`: при `tree == []` в `.elem.json` вызывается
  `extract_legacy_form_elements` (best-effort). При успехе `elem_index_ok=True`,
  `extraction_source="legacy_form_json"`; при неудаче — поведение прежнее
  (`elem_index_ok=False`, warning «Элементы формы не найдены»)
  (issue #100, PR #102).
- `form_classifier.classify_empty_tree_form()`: константа
  `PLATFORM_OBJECT_FORM_NAMES` обновлена до 17 имён (добавлено `ФормаОбъекта`)
  (issue #100, PR #102).
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

- **`form_context.build_form_context(..., *, type_resolver=None)`** — контекст
  формы больше не терял читаемые имена ссылочных типов. Резолвер (обычно
  `FormScanIndex.resolve_reference_type`, #88) пробрасывается в
  `object_decoder.decode_object_attributes()`, поэтому
  `FormContext.object_attributes` содержит `CatalogRef.<Имя>` вместо
  `Ref#<uuid>` там, где UUID уже известен индексу. Параметр keyword-only:
  вызовы `build_form_context(entry, root)` работают без изменений, без
  резолвера поведение идентично прежнему, неизвестный UUID сохраняет
  безопасный fallback `Ref#<uuid>`, исключение резолвера уходит в
  `REF_RESOLVER_FAILED` и не прерывает сборку. Второй индекс не создаётся,
  `decode_object_attributes`, `catalog_resolver.resolve_data_path()` и
  `FormScanIndex` не менялись, `resolved_relations` не затронуты. Новый модуль
  `tests/test_form_context_issue147.py` — 11 тестов, фикстуры синтетические;
  846 -> 857 passed (issue #147, PR #162).
- `catalog_resolver.resolve_data_path()` больше не разбирает файл объекта
  самостоятельно и использует `object_decoder.decode_object_attributes()` как
  единственную точку декодирования raw-header. До исправления на production
  выгрузке (`Catalog/Номенклатура/CatalogForm/ФормаЭлемента`) все 65 записей `resolved_relations`
  возвращались с `resolved=false` и `synonym=null`, потому что верхнеуровневый
  ключ `header` не распознавался: функция искала нормализованные
  `Properties` / `Attributes`. После исправления резолвятся как верхнеуровневые
  реквизиты, так и реквизиты табличных частей; на той же форме
  40 `resolved=true` из 65, остальные
  25 — категории #147 / #143. Удалены `_get_attributes_section`
  и `_get_tabular_attributes_section`; добавлен ограниченный кэш по
  `(path, mtime_ns, size)` и `clear_object_cache()`. Публичная сигнатура,
  формат `ResolvedBinding` и `FormContext.resolved_relations` не изменились;
  best-effort контракт сохранён — issue #148, PR #159.
- `docs/form_classifier.md` — две относительные ссылки `../../issues/98` и
  `../../pull/99` заменены на абсолютные URL. Прежний вид резолвился только
  при рендере на github.com и был битым в локальном просмотре и в любом
  внешнем рендерере Markdown.
- Докстринги `examples/extract_skd_queries.py` и
  `examples/legacy_list_form_bindings.py` — явно указано, что скрипты требуют
  реальной выгрузки и обязательных аргументов, а завершение по ошибке
  `argparse` при запуске без аргументов является ожидаемым поведением.

- Индекс ссылочных типов на реальных данных сначала давал 0 резолюций:
  постановка #88 предполагала UUID объекта в `header[0][1][2]`, но ссылка
  реквизита адресует соседние слоты того же блока идентификации — на выборке
  из 300 ссылочных UUID слот `[1]` дал 152 совпадения, слот `[3]` — 141.
  Индексируются все валидные UUID блока `header[0][1]`; они принадлежат одному
  объекту, поэтому имя типа для них одно (issue #88, PR #118).
- **Формы с непустой картой реквизитов давали нулевую привязку `data_path`.**
  Элементы таких форм не ссылаются на реквизиты объекта по UUID: они адресуют
  вложенное поле реквизита формы цепочкой сегментов в том же слоте `raw[11]`,
  где у поддержанного ранее layout лежит одиночная ссылка. Существующий
  декодер собирал UUID рекурсивно, не находил их в карте объекта и оставлял
  элемент без привязки. На проверенной выгрузке из 2216 форм цепочечный
  декодер дал 739 привязок в 111 формах, из них 377 новых; 362 совпали с
  результатом прежнего структурного fallback, изменённых путей — 0.
  Общее число подтверждённых привязок выросло с 13352 до 13729 при неизменном
  составе элементов (50573 всего, 15887 элементов данных). Число форм с
  непустой картой и нулевой привязкой сократилось с 43 до 42
  (issue #89, PR #115).
- **`elem_parser._standard_attribute_map`** читала `data[0][1]` вместо
  `data["header"][0][1]`. Для dict-layout карта стандартных реквизитов
  всегда возвращалась пустой, из-за чего `Наименование` и `Код` не
  попадали в `attr_map` (issue #107, PR #110).
- **`elem_parser._tabular_field_attribute_slots`**: `walk_refs` распознаёт
  Pattern-ссылки `["#", UUID]` наряду с `["0", UUID]`. UUID стандартных
  реквизитов в формах-списках лежат в Pattern-блоке под тегом `"#"`.
  Закрыто 26 форм live-базы (issue #107, PR #110).
- **77 неиндексируемых форм не имели машиночитаемой причины.** После #103
  формы с `elem_index_ok=False` давали единственное общее предупреждение
  «Элементы формы не найдены», из-за чего нельзя было отличить норму
  (форма без виджетов данных) от пробела декодера. `classify_unindexed_form()`
  присваивает каждой такой форме конкретную причину. Прогон на полной
  конфигурации УТ 10.3: 2216 форм, проиндексировано 2139 (96.5%),
  неиндексировано 77 — **C** 17, **B** 48, **A** 12, **D** 0, **unknown** 0.
  Ложных `data_path` не создано, исключений 0 (issue #105, PR #106).
- **Legacy `ФормаСписка` / `ФормаВыбора` с пустым `tree` не индексировались.**
  Реализовано безопасное кросс-чтение JSON формы и метаданных владельца.
  Проверка на полной конфигурации: 2216 форм, OK 2139, fallback #103 — 160,
  ERROR 77 (до исправления 224), исключений 0, невалидных результатов #103 — 0.
  Полная регрессия: 487 тестов. Оставшиеся форматы вынесены в #105
  (issue #103).
- **49 обычных форм не возвращали элементы при `tree: []`.** ФормаЗаписи
  регистров сведений, ФормаЭлемента справочников, формы отчётов и обработок.
  Исправлено чтением `form/*.json` через `extract_legacy_form_elements`
  (issue #100, PR #102). Проверено на УТ 10.3: 3 `ФормаЗаписи` регистров
  сведений и 7 основных форм `Форма` отчётов/обработок теперь имеют
  `elem_index_ok=True`. Оставшиеся 225 форм (`ФормаСписка`/`ФормаВыбора`) —
  следующая задача (#103).
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
