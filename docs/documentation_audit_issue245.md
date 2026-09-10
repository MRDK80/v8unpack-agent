# Аудит документации (issue #245)

Дата аудита: 10 сентября 2026  
База: `main` = `c06e9f5df0b2ce6142b1f1916a95a17a6b20d5be`  
Ветка работ: `docs/245-readme-docs-refactor`

Аудит выполнен в read-only режиме до внесения правок. Production-код не
изменяется. Границы задачи: `examples/*.py` относятся к #246, финальный
integration gate — #210.

## 1. Подтверждённый baseline

```text
ruff check .              RC=0
mypy v8unpack_agent       RC=0, 25 source files
python -m pytest -q       1087 passed
README.md                 30123 bytes
```

Целевой размер README: не более 20 000 bytes UTF-8, то есть сокращение
минимум на 10 123 bytes (около 33.6%) исключительно переносом материала и
устранением дублей.

## 2. Фактический inventory

```text
3   корневых Markdown
18  профильных Markdown в docs/
7   research Markdown в docs/research/
28  всего в scope аудита
```

При повторной инвентаризации обнаружен ранее пропущенный
`docs/skd_extractor.md`, поэтому фактический scope — 28 файлов, а не 27.
Критерий issue #245 «все Markdown-файлы в `docs/`» этим не нарушен.

Служебные артефакты вне Markdown inventory, ссылки на которые проверяются в
link audit:

```text
docs/lint_baseline_issue161.txt
docs/scan_baseline_A.json
docs/scan_baseline_B.json
docs/scan_baseline_C.json
docs/scan_baseline_external.json
```

## 3. Семантика verdict

- `current` — документ соответствует `main`, имеет ясную роль, содержательная
  правка не требуется.
- `updated` — документ исправляется или структурно перерабатывается в #245.
- `historical` — документ фиксирует датированный research или baseline и не
  переписывается как актуальное руководство; при необходимости добавляется
  датированный addendum.

## 4. Audit table

| Path | Role | Verdict | Canonical contracts | Duplicates | Action |
|---|---|---|---|---|---|
| `README.md` | landing page | updated | overview, navigation | API-справочники, классификация форм, корпусные числа, перечень примеров | сократить до навигации, перенести справочники |
| `CHANGELOG.md` | исторический журнал | historical | история релизов | нет | добавить запись в `## [Unreleased]`, историю не править |
| `CONTRIBUTING.md` | процесс контрибуции | updated | процесс, запуск тестов | инструкции по тестам дублируются в README | принять раздел о тестах из README |
| `docs/IMPLEMENTATION_STATUS.md` | living status | updated | текущее состояние реализации | current-state утверждения, пересекающиеся с README | обновить baseline, развести роль с README и CHANGELOG |
| `docs/pipeline.md` | canonical | updated | pipeline, `FormUnpacker`, `unpack_all_forms`, `unpack_erf`, `update_forms_index` | walkthrough из README | принять описание цепочки discovery → unpacker |
| `docs/runner.md` | canonical | updated | runner, CLI, exit codes 0/2/3/4/5/6 | развёрнутый блок Production runner в README | принять детали запуска и коды возврата |
| `docs/run_report.md` | canonical | updated | post-run report, `SCHEMA_VERSION = 1`, инварианты отчёта | нет | снять статус документа-сироты, добавить ссылку из README |
| `docs/scan_forms.md` | canonical | updated | discovery, `FormScanIndex`, `INDEX_SCHEMA_VERSION = 2`, контракт относительных путей | описание scan_forms в README | явно зафиксировать версию схемы индекса |
| `docs/elem_parser.md` | canonical | current | `parse_elem_json`, `UnindexedReason`, `PLATFORM_DYNAMIC_SOURCE_MARKER` | нет | ссылка на `chain_data_path.py` проверена и валидна |
| `docs/form_classifier.md` | canonical | updated | `classify_form`, `FormClass`, `calc_data_path_coverage`, `CoverageReport` | блок классификации форм в README | принять раздел классификации и метрику покрытия |
| `docs/form_context.md` | canonical | current | `FormContext`, `to_llm_prompt_fragment`, контракт усечения | нет | без изменений |
| `docs/form_summary.md` | canonical | current | `build_form_summary`, `to_normalized_json` | нет | без изменений |
| `docs/form_router.md` | canonical | current | `FormRouter`, `form_paths` | нет | без изменений |
| `docs/object_decoder.md` | canonical | updated | `decode_object_attributes`, `DecodeResult` | таблица ссылочных типов реквизитов в README | принять раздел о ссылочных типах |
| `docs/catalog_resolver.md` | canonical | current | `resolve_data_path`, `ResolvedBinding` | нет | без изменений |
| `docs/common_modules.md` | canonical | current | `scan_common_modules`, typed read status | нет | без изменений |
| `docs/skd_extractor.md` | canonical | current | `extract_skd_queries`, `extract_all_skd_queries` | нет | добавить в навигацию README |
| `docs/managed_forms_structure.md` | canonical | current | discovery управляемых форм, `.elem.json` | нет | без изменений |
| `docs/external_forms_structure.md` | canonical | current | структура внешних обработок и отчётов | нет | без изменений |
| `docs/drift_checker.md` | canonical | current | `check_drift`, `DriftReport` | нет | без изменений |
| `docs/branch_protection.md` | policy | current | политика ветвления | нет | без изменений |
| `docs/research/form_bin_issue150.md` | research | historical | наблюдения по структуре формы | нет | сохранить как есть |
| `docs/research/missing_object_attributes_issue163.md` | research | historical | причины пустых реквизитов объекта | нет | сохранить как есть |
| `docs/research/non_reference_type_facets_issue166.md` | research | historical | неcсылочные фасеты типов | нет | сохранить как есть |
| `docs/research/platform_types_cross_config_issue164.md` | research | historical | кросс-конфигурационная проверка типов | нет | сохранить как есть |
| `docs/research/ref_resolver_issue143.md` | research | historical | разрешение ссылочных типов | нет | сохранить как есть |
| `docs/research/third_configuration_validation.md` | research | historical | валидация на третьем корпусе | нет | сохранить как есть |
| `docs/research/unindexed_share_issue229.md` | research | historical | доля неиндексированных форм | нет | стать адресатом ссылки из README вместо чисел в тексте |

Всего строк: 28. Каждый файл присутствует ровно один раз.

`examples/README.md` в таблицу не входит: его рефакторинг относится к #246, в
#245 проверяются только ссылки на него.

## 5. Расхождения README с production-кодом

Проверка выполнена по фактическим сигнатурам, а не по прежней документации.

1. Python quick start использует распаковщик с тремя аргументами и передаёт
   его напрямую в `unpack_all_forms`. Фактически `unpack_all_forms` вызывает
   `unpacker(source, unpacked_root)` и передаёт `FormBinSource`, поэтому
   пример несовместим с текущим контрактом. Legacy-функция допустима только
   через `adapt_legacy_unpacker`.
2. `discover_form_sources` отсутствует в исполняемом примере, хотя является
   каноническим discovery API. `discover_form_bins` сохранён как legacy shim
   и внутри `unpack_all_forms` больше не используется.
3. Отбор форм: `form_ids` каноничен, `form_names` оставлен для совместимости и
   при неоднозначном имени поднимает `AmbiguousFormNameError`. В README это не
   отражено.
4. Три различные версии схем нигде не разведены: post-run report `1`,
   `FormsIndex` `2`, индекс сканирования `2` при legacy-значении `1`.
5. `docs/run_report.md` не имеет ни одной входящей ссылки из README.
6. Команда установки тестовых зависимостей приведена в нерабочей форме без
   кавычек вокруг extras.
7. Корпусные числа в разделе о верификации приводятся как текущие, хотя
   актуальные значения зафиксированы в датированном research по #229.

Ссылка на `chain_data_path.py` из `docs/elem_parser.md` проверена: модуль
существует, правка не требуется.

Проверка `CoverageReport` и `calc_data_path_coverage` в runner и в модуле
отчёта совпадений не дала: метрика покрытия не сериализуется в post-run
report. Поэтому её каноническое место — `docs/form_classifier.md`, где раздел
интеграции уже существует. Отдельный документ не создаётся, чтобы не возникло
двух мест, претендующих на канон одного контракта.

## 6. Целевая структура README

| Раздел | Бюджет, bytes | Источник |
|---|---:|---|
| Назначение и non-goals | 1500 | вводная часть, дополнить границами поддержки |
| Кто что решает | 600 | без существенных изменений |
| Установка | 900 | текущий способ установки, пометка о том, что публикация в индексе пакетов отслеживается в #149 |
| CLI quick start | 1200 | фактическая команда запуска и коды возврата списком |
| Python quick start | 1800 | переписать на канонический discovery и `FormUnpacker` |
| Матрица pipelines | 2500 | компактная схема без развёрнутых перечислений |
| Ключевые возможности | 1200 | краткий список |
| Публичная поверхность | 2500 | таблица «модуль → имена → канонический документ» |
| Документация | 3500 | шесть навигационных групп, все 18 профильных и 7 research документов |
| Известные ограничения | 900 | отсутствие production-распаковщика одиночного бинарного файла формы, внешний семантический индекс вне scope пакета |
| Связанное и лицензия | 1200 | без изменений |

Сумма бюджета — около 17 800 bytes, запас до предела примерно 2 200 bytes.

## 7. Карта переноса

```text
Классификация форм и метрика покрытия  → docs/form_classifier.md
Ссылочные типы реквизитов              → docs/object_decoder.md
Развёрнутый запуск и коды возврата     → docs/runner.md
Схема отчёта о запуске                 → docs/run_report.md
Пошаговый разбор пайплайна             → docs/pipeline.md
Корпусные наблюдения                   → docs/research/unindexed_share_issue229.md
Перечень исполняемых примеров          → examples/README.md (#246)
Инструкции по запуску тестов           → CONTRIBUTING.md
```

Навигация строится так, чтобы подробности достигались не более чем за один
переход из README, и чтобы на один контракт не приходилось двух ссылок,
обещающих быть каноническими.

## 8. Обезличенность

Правило разграничено по слою:

- весь переписываемый текст, включая README и принимающие профильные
  документы, использует только синтетические нейтральные имена и не содержит
  наименований конкретных конфигураций и прикладных объектов;
- датированные research-документы сохраняются без изменений, поскольку их
  наблюдения не переписываются задним числом.

Проверяется отсутствие абсолютных локальных путей, идентификаторов,
исходных фрагментов кода прикладных решений и любых сведений о внутренней
инфраструктуре.

## 9. Placeholder audit

Поиск по README, CHANGELOG, CONTRIBUTING и `docs/` дал единственное
совпадение — историческую формулировку в записи журнала изменений. Это
историческая цитата, а не незаполненный placeholder; правка не требуется.

## 10. Порядок дальнейших шагов

1. Обновить профильные документы-приёмники.
2. Сократить README и выстроить навигацию.
3. Обновить `docs/IMPLEMENTATION_STATUS.md` и `CHANGELOG.md`.
4. Проверить ссылки, отсутствие placeholders и размер README в bytes.
5. Фактически выполнить оба quick start.
6. Прогнать линтер, проверку типов и полный набор тестов.
7. Провести ручную проверку обезличенности и открыть pull request.
