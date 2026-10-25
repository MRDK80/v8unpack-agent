# Матрица поискового покрытия видов артефактов (#372)

Документ сверяет матрицу BSL-покрытия #209
([`bsl_coverage_matrix.md`](bsl_coverage_matrix.md), итог эпика #201 с
дополнениями #337 и #351) с фактическим поисковым контуром эпика #320:
корпус #323, фрагментация #324, индекс #325/#362, диспетчер #326, контекст
#327, библиотека и CLI #328. Приёмочная версия: `epic/320-unified-search`
@ `f18e398ca98f3d74f093d83dfacdf0bb3fd9e5e3`. Измеренное качество относится
к runtime `7124fda57aa1f1b5fbc34fda56082f711c273754` (#370) и не переносится
на другие SHA автоматически.

Статусы разведены намеренно: «обнаруживается» (сканер), «включается в
корпус» (есть документ с текстом), «exact» (точное разрешение по ключам),
«semantic» (смысловой путь через явно переданный эмбеддер), «связь
владельца» (`module_entry` / `form_key` / `unconfirmed`) и «измерено»
(#370). Принятие ключей конструкторами не доказывает извлечение сканером;
индексация не означает прохождение порога качества; зелёный CI не
доказывает релевантность; отсутствие теста не доказывает отсутствие
реализации.

## Общие правила контура

- Корпус создаёт документ только из прочитанного непустого текста
  (`search_corpus.py`, `_add_module` / `_add_form`). Причины исключения
  видны в `CorpusReport`: `empty`, `whitespace_only`, `missing`,
  `read_error`, `unsafe_path`, `content_changed`, `duplicate_artifact`,
  `form_module_via_form_context`, `invalid_identity`.
- Связь `module_entry` создаётся для модуля даже без текста; карточка
  владельца строится по подтверждённым связям, поэтому владелец может быть
  найден по `artifact_id` карточки и при исключённом тексте модуля.
- Exact (`search_dispatch.py`, `ExactCatalog.resolve`): весь запрос после
  `strip()` по `identity_key`; шаг 1 — `artifact_id` любого документа,
  шаг 2 — `form_key[3]` формы, шаг 3 — `owner.owner_name` артефакта с
  установленным владельцем. Карточка владельца — только по `artifact_id`.
  Синоним, подстрока, `Тип.Имя`, короткое имя команды и нормализация
  пробелов алиасами не являются.
- Semantic: `query_index` (#325/#362), один лучший фрагмент на артефакт,
  только при явно переданных индексе и эмбеддере; иначе
  `error/index_not_ready`. Автоматических загрузок моделей и сети нет
  (`tests/test_search_api_issue328.py::test_module_has_no_hidden_providers`,
  guard-фикстура #371).
- `hashed-bow-demo` — демонстрационная механика, не качество поиска
  (`docs/search_cli.md`, help CLI). Оценочная модель — BAAI/bge-m3 (#357,
  #370); production default не выбран.

## Матрица по видам артефактов

Столбцы: обнаружение по #209; включение в корпус; exact; semantic; связь
владельца; доказательства; измерено в #370 (free, BGE-M3, `recall@5` /
`mrr@10`, пороги 0.80 / 0.60).

| Вид (`module_kind` / `owner_kind`) | Обнаружение (#209) | Корпус (#323) | Exact | Semantic | Связь владельца | Доказательства | Измерено (#370) |
|---|---|---|---|---|---|---|---|
| `ordinary_application`, `managed_application`, `external_connection`, `session` / `configuration` | `scan_configuration_modules` (#204) | включается | `artifact_id` `configuration:::<kind>`; `owner_name` отсутствует | да | `module_entry` | `test_search_corpus_issue323.py::test_every_accepted_module_kind_goes_through_contract`; `test_search_e2e_issue371.py` (`Configuration.802.bsl`) | issue369: 1.0000 / 1.0000, 1.0000 / 0.8542, 1.0000 / 0.6542, 1.0000 / 1.0000 (по 8); issue321 session 1.0000 / 1.0000 (n=1) |
| `object` / `metadata_object` | `scan_metadata_object_modules` (#205) | включается | `artifact_id`; `owner_name` | да | `module_entry` | #323, #370 | issue369: 1.0000 / 0.8542 (n=8); issue321: 1.0000 / 0.8056 (n=6) |
| `manager` / `metadata_object` | #205 | включается | `artifact_id`; `owner_name` | да | `module_entry` | #323; пример `unified_search.py` (фильтр `module_kind=manager`) | issue369: 1.0000 / 0.8750 (n=8) |
| `record_set` / `metadata_object` | `scan_record_set_modules` (#206) | включается | `artifact_id`; `owner_name` | да | `module_entry` | #323; пример | issue369: 1.0000 / 0.7750 (n=8) |
| `command` / `common_command` | `scan_command_modules` (#207) | включается | `artifact_id`; `owner_name` = имя команды | да | `module_entry` | #323 (`owner_kinds` содержит `common_command`) | issue369: 0.8750 / 0.8958 (n=8) |
| `command` / `metadata_object_command` | #207 | включается | `artifact_id`; `owner_name` = `<Объект>.<Команда>`; короткое имя — не алиас | да | `module_entry` | #323 | issue369: 1.0000 / 0.9167 (n=8) |
| `value_manager` / `metadata_object` (Constant) | `scan_value_manager_modules` (#337) | включается | `artifact_id`; `owner_name` | да | `module_entry` | #323; #371 (`Constant/Rate`, ambiguous по `Rate`) | issue369: 1.0000 / 0.9167 (n=8) |
| `service` / `metadata_object` (HTTPService, WebService) | `scan_service_modules` (#337) | включается | `artifact_id`; `owner_name` | да | `module_entry` | #323; #328 и #371 (`HTTPService/Api`, `HTTPService/Rate`) | issue369: HTTPService 1.0000 / 0.8542, WebService 0.8750 / 0.6042 (по 8) |
| `object` / `external_data_processor`, `external_report` | `scan_external_object_modules` (#351), только `mode="external"` | включается | `artifact_id`; `owner_name` из `ExternalDataProcessor.json` | да | `module_entry` | `test_search_corpus_issue323.py::test_external_object_module_and_form`; `test_search_api_issue328.py::test_external_scanner_only_in_external_mode` | issue369: 1.0000 / 0.8750 и 1.0000 / 0.9375 (по 8) |
| `common_module` / `common_module` | `scan_common_modules` (#151) | включается; повтор из `ModuleIndex` — `duplicate_artifact` | `artifact_id`; `owner_name` | да | `module_entry` | `test_common_module_counted_once`; #328/#371 (`CommonModule/Shared`) | issue369: **0.7500** / 0.6146 (n=8) — ниже порога `recall@5`; issue321: 1.0000 / 0.6000 (n=2) |
| `form` / `metadata_object_form` (4-уровневый layout) | `scan_forms` + `FormContext` | включается при непустом `bsl_text`; elem-only — `missing` | `artifact_id` `form:<T>/<N>/<C>/<F>`; `form_name`; `owner_name` | да | `form_key` | `test_owner_links_and_cards`, `test_form_statuses_and_read_failure`, `test_form_module_is_not_second_instance`; `test_search_forms_e2e_issue384.py` (сквозной exact/ambiguous/semantic); пример `unified_search.py` (exact по id формы, ambiguous по `ФормаКарточки`) | issue369: 1.0000 / 0.7917 (n=8); issue321: 1.0000 / 1.0000 (n=2) |
| `form` / `common_form` (`CommonForm/<Форма>`) | `scan_forms` | включается | `artifact_id` `form:CommonForm//CommonForm/<Форма>`; `form_name`; по `owner_name` не находится | да | `unconfirmed`; в карточки не входит | `test_owner_links_and_cards`; `test_search_forms_e2e_issue384.py` | issue369: 0.8750 / 0.7991 (n=8) |
| `form` внешних обработок (`Form/<Форма>`) | `scan_forms` (`mode="external"`) | включается | `artifact_id`; `form_name`; по `owner_name` не находится | да | `unconfirmed` к владельцу #351 | `test_external_object_module_and_form`; `test_search_forms_e2e_issue384.py` | issue369: 0.8750 / **0.3917** (n=8) — ниже порога `mrr@10` |
| `form` внешних отчётов (`ReportForm/<Форма>`) | `scan_forms` (`mode="external"`) | включается | как у внешних обработок | да | `unconfirmed` | `test_search_forms_e2e_issue384.py` | issue369: 1.0000 / **0.4875** (n=8) — ниже порога `mrr@10` |
| карточка владельца `owner:<kind>:<type>:<name>` | строится корпусом | включается для владельцев с подтверждённой связью | только `artifact_id` | да | — | `test_owner_links_and_cards`; #371 (`owner_name` фильтр) | отдельно не измерялась |

## Не поддержано и не проверено

- Вне индекса модулей (остаток #209): `Sequences`, `obj` у
  `ChartOfAccounts`, `ChartOfCalculationTypes`, `AccountingRegister`;
  модули расширений; raw-layout. Эти файлы не попадают в корпус и в поиск.
- Внешний отчёт без форм и без суффикса `.erf` неотличим от обработки:
  записи нет (#351).
- Связи `CommonForm` и внешних форм остаются `unconfirmed`; статус не
  повышается документацией; карточек владельца для них нет.
- Exact по синониму не поддержан. В измерении #370 запрос e4 набора #321
  оставался в знаменателе (7/8); после #385 он находится по имени с
  нормализацией разделителей, exact 8/8 на текущем runtime подтверждён
  в #383.
- Сквозная проверка форм `scan_forms → collect_search_corpus →
  fragment_corpus → снимок → search_api/search_cli → контекст` выполнена
  на синтетических выгрузках в #384 (`test_search_forms_e2e_issue384.py`)
  для формы объекта конфигурации, `CommonForm`, формы внешней обработки и
  формы внешнего отчёта: обнаружение из файлов, исключение пустого BSL,
  exact по id и по уникальному `form_name`, полный набор при коллизии
  имени, semantic с фильтром `form` (уникальность и `top_k`), совпадение
  Library и CLI, координаты и бюджет контекста. Эмбеддер демонстрационный:
  проверена механика, а не качество ранжирования форм.
- Негативный порог отказа semantic не задан: пустой gold не требует
  `empty`; распределение top-1 similarity на negative — диагностика.
- Данные #370 синтетические; процедуры заданы комментариями, пары одного
  `intent_id` зависимы; статистическая достаточность не заявляется.

## Пересчёт чисел

Числа столбца «Измерено» независимо пересчитаны по первичному
`research/search_quality_issue370_results.json` (SHA-256
`e8dcdbb07df887e7374fa8f9bf6cd778bf20532b2b2753690aeae3f7e46cfe02`) из
`per_query[].top10` и `gold` (artifact-level, query-macro по `class=free`)
и из элементов `exact_controls`; знаменатели — `n_free` среза. Корпусные
итоги: issue321 free n=17 — `recall@5` 1.0000, `mrr@10` 0.8402; issue369
free n=152 — 0.9605 / 0.7946. Exact 7/8, ambiguous 4/4 и 8/8, ложных exact
на negative 0/7 и 0/8. Связи issue369: 28 подтверждённых, 6 `unconfirmed`.
Для issue369 использованы срезы `group:*` (поле `group` запроса); для
issue321 вид артефакта восстановлен из `gold` (`form:` либо последний
сегмент `module_id`), поэтому срезы по виду пересчитаны по этому правилу,
а не взяты из сводок отчёта. Отчёт и первичный JSON не изменялись.

## Границы приёмки

Документ фиксирует покрытие на указанном SHA. Группы ниже планки на
срезах с n ≤ 8 приняты как известное ограничение решением владельца
в #383 (правило Б); пробел сквозной проверки форм закрыт в #384.
`unconfirmed` связи `CommonForm` и внешних форм остаются ограничением.
Пороги и эталоны не менялись.
