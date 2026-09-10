# Аудит документации (issue #245)

Дата аудита: 10 сентября 2026  
База: `main` = `c06e9f5df0b2ce6142b1f1916a95a17a6b20d5be`  
Ветка работ: `docs/245-readme-docs-refactor`

Аудит выполнен до внесения правок, вердикты уточнены по факту чтения
документов. Production-код не изменялся. Границы задачи: `examples/*.py`
относятся к #246, финальный integration gate — #210.

## 1. Baseline и результат

Состояние до начала работ и после завершения docs-правок:

| Проверка | До | После |
|---|---|---|
| `ruff check .` | RC=0 | RC=0 |
| `mypy v8unpack_agent` | RC=0, 25 source files | RC=0, 25 source files |
| `python -m pytest -q` | 1087 passed | 1087 passed |
| размер `README.md` | 30123 bytes | 14830 bytes |
| битые относительные ссылки | не измерялось | 0 |

Цель по README — не более 20 000 bytes UTF-8. Фактическое сокращение —
15 293 bytes, то есть 50.8% при требуемых 33.6%; запас до порога —
5 170 bytes. Сокращение достигнуто переносом материала и устранением дублей.

Link audit выполнен одноразовым локальным анализом по README, `docs/`,
`docs/research/` и `examples/README.md`. Скрипт проверки в репозиторий не
добавлялся.

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

Сам этот аудит — новый документ и в таблицу из 28 строк не входит.

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

- `current` — документ соответствует коду, имеет ясную роль, содержательная
  правка не потребовалась.
- `updated` — документ исправлен или структурно переработан в #245.
- `historical` — документ фиксирует датированный research или baseline и не
  переписывается как актуальное руководство.

Итоговое распределение: 5 `updated`, 15 `current`, 8 `historical`.

Четыре документа переведены из предварительного `updated` в `current`:
чтение показало, что `runner.md`, `run_report.md`, `object_decoder.md` и
`elem_parser.md` уже полны и точны, а дублировал их именно README.
Сиротство `run_report.md` устранено ссылками из README, а не правкой самого
документа.

## 4. Audit table

| Path | Role | Verdict | Canonical contracts | Duplicates | Action |
|---|---|---|---|---|---|
| `README.md` | landing page | updated | overview, navigation | API-справочники, классификация форм, корпусные числа, перечень примеров | сокращён до 14830 bytes, справочники вынесены |
| `CHANGELOG.md` | исторический журнал | historical | история релизов | нет | запись в `## [Unreleased]`, история не правится |
| `CONTRIBUTING.md` | процесс контрибуции | current | процесс, запуск тестов | нет | README ссылается вместо дублирования |
| `docs/IMPLEMENTATION_STATUS.md` | living status | updated | текущее состояние реализации | current-state утверждения, пересекающиеся с README | обновить baseline и ссылки на #245 |
| `docs/pipeline.md` | canonical | updated | pipeline, `FormUnpacker`, `FormsIndex` schema 2 | walkthrough из README | принят канонический вызов, legacy-адаптер и таблица версий схем |
| `docs/runner.md` | canonical | current | runner, CLI, exit codes 0/2/3/4/5/6 | развёрнутый блок в README | дубль удалён из README, документ не правился |
| `docs/run_report.md` | canonical | current | post-run report, `SCHEMA_VERSION = 1` | нет | получил входящие ссылки из README |
| `docs/scan_forms.md` | canonical | updated | discovery, `FormScanIndex` schema 2, коды предупреждений | описание сканера в README | удалено упоминание приватного движка |
| `docs/elem_parser.md` | canonical | current | `parse_elem_json`, `UnindexedReason`, `PLATFORM_DYNAMIC_SOURCE_MARKER` | нет | ссылка на `chain_data_path.py` проверена и валидна |
| `docs/form_classifier.md` | canonical | updated | `classify_form`, `FormClass`, `calc_data_path_coverage`, `CoverageReport` | блок классификации в README | принят раздел классификации и канон метрики покрытия |
| `docs/form_context.md` | canonical | current | `FormContext`, `to_llm_prompt_fragment`, контракт усечения | нет | без изменений |
| `docs/form_summary.md` | canonical | current | `build_form_summary`, `to_normalized_json` | нет | без изменений |
| `docs/form_router.md` | canonical | current | `FormRouter`, `form_paths` | нет | без изменений |
| `docs/object_decoder.md` | canonical | current | `decode_object_attributes`, `DecodeResult`, агрегаты #84 и #88 | таблица ссылочных типов в README | дубль удалён из README |
| `docs/catalog_resolver.md` | canonical | current | `resolve_data_path`, `ResolvedBinding` | нет | без изменений |
| `docs/common_modules.md` | canonical | current | `scan_common_modules`, typed read status | нет | без изменений |
| `docs/skd_extractor.md` | canonical | current | `extract_skd_queries`, `extract_all_skd_queries` | нет | добавлен в навигацию README |
| `docs/managed_forms_structure.md` | canonical | current | discovery управляемых форм, `.elem.json` | нет | без изменений |
| `docs/external_forms_structure.md` | canonical | current | структура внешних обработок и отчётов | нет | без изменений |
| `docs/drift_checker.md` | canonical | current | `check_drift`, `DriftReport` | нет | без изменений |
| `docs/branch_protection.md` | policy | current | политика ветвления | нет | без изменений |
| `docs/research/form_bin_issue150.md` | research | historical | наблюдения по структуре формы | нет | сохранён как есть |
| `docs/research/missing_object_attributes_issue163.md` | research | historical | причины пустых реквизитов объекта | нет | сохранён как есть |
| `docs/research/non_reference_type_facets_issue166.md` | research | historical | нессылочные типовые грани | нет | сохранён как есть |
| `docs/research/platform_types_cross_config_issue164.md` | research | historical | кросс-конфигурационная проверка типов | нет | сохранён как есть |
| `docs/research/ref_resolver_issue143.md` | research | historical | разрешение ссылочных типов | нет | сохранён как есть |
| `docs/research/third_configuration_validation.md` | research | historical | валидация на третьей выгрузке | нет | сохранён как есть |
| `docs/research/unindexed_share_issue229.md` | research | historical | доля неиндексированных форм | нет | стал адресатом ссылки из README вместо чисел в тексте |

Всего строк: 28. Каждый файл присутствует ровно один раз.

`examples/README.md` в таблицу не входит: его рефакторинг относится к #246, в
#245 проверены только ссылки на него.

## 5. Найденные дефекты и их статус

Проверка выполнена по фактическим сигнатурам, а не по прежней документации.

| № | Дефект | Статус |
|---:|---|---|
| 1 | Python quick start передавал трёхаргументный распаковщик в `unpack_all_forms`, тогда как пайплайн вызывает `unpacker(source, unpacked_root)` с `FormBinSource` | исправлено |
| 2 | `discover_form_sources` отсутствовал в описании, хотя является каноническим discovery API | исправлено |
| 3 | Не было сказано, что `form_ids` каноничен, а `form_names` при неоднозначном имени даёт `AmbiguousFormNameError` | исправлено |
| 4 | Три версии схем не были разведены | исправлено |
| 5 | `docs/run_report.md` не имел входящих ссылок | исправлено |
| 6 | Команда установки тестовых зависимостей была нерабочей | исправлено |
| 7 | Корпусные числа приводились как текущие | исправлено |
| 8 | `docs/scan_forms.md` содержал ссылку на приватный движок с его именем и номером issue | исправлено |

Дефект 8 найден по ходу аудита и к исходному списку не относился: публичный
репозиторий раскрывал существование приватного репозитория. Контрольный поиск по
README, `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/` и `examples/` после правки даёт
только совпадения слова «приватный» в значении приватных членов Python.

Ссылка на `chain_data_path.py` из `docs/elem_parser.md` проверена: модуль
существует, дефектом не является.

Проверка `CoverageReport` и `calc_data_path_coverage` в `runner.py` и
`run_report.py` совпадений не дала: метрика покрытия не сериализуется в
post-run report. Поэтому её каноническое место — `docs/form_classifier.md`;
отдельный документ не создавался, чтобы не возникло двух мест, претендующих
на канон одного контракта.

Дополнительно в `docs/form_classifier.md` устранены два дефекта самого документа:
дублированный абзац о категориях и ошибочный итог «47 форм» при слагаемых
17 + 26 + 2.

## 6. Структура README после сокращения

Порядок разделов: назначение, границы и non-goals, распределение
ответственности, установка, CLI quick start, Python quick start, матрица
пайплайна, ключевые возможности, версии схем, публичная поверхность,
навигация, известные ограничения, тесты, связанное, лицензия.

Навигация разделена на шесть групп: начало работы, пайплайн и запуск,
формы и разбор, метаданные и индексы, исследования, примеры и политики.
Каждый из 18 профильных и 7 research документов достигается за один переход.

## 7. Карта переноса

```text
Классификация форм и метрика покрытия  → docs/form_classifier.md
Пошаговый разбор пайплайна             → docs/pipeline.md
Ссылочные типы реквизитов              → docs/object_decoder.md
Развёрнутый запуск и коды возврата     → docs/runner.md
Схема отчёта о запуске                 → docs/run_report.md
Корпусные наблюдения                   → docs/research/unindexed_share_issue229.md
Перечень исполняемых примеров          → examples/README.md (#246)
Инструкции по запуску тестов           → CONTRIBUTING.md
```

Ни один контракт не получил двух мест, претендующих на каноничность.

## 8. Обезличенность

Правило разграничено по слою:

- весь переписанный текст, включая README и `docs/form_classifier.md`, использует
  только синтетические нейтральные имена;
- датированные research-документы сохранены без изменений, их наблюдения не
  переписываются задним числом.

Стандартные имена форм платформы сохранены: это номенклатура платформы,
необходимая для описания контрактов, а не идентификация конкретной
конфигурации.

В документах с вердиктом `current` остались имена примеров из типовой
номенклатуры. Они не идентифицируют развёртывание, хосты или строки
подключения; единый стандарт примеров по всему репозиторию — отдельная задача
за пределами #245.

Проверено отсутствие абсолютных локальных путей, идентификаторов, исходных
фрагментов кода прикладных решений и сведений о внутренней инфраструктуре.

## 9. Placeholder audit

Поиск по README, CHANGELOG, CONTRIBUTING и `docs/` дал единственное
совпадение — историческую формулировку в записи журнала изменений. Это
историческая цитата, а не незаполненный placeholder; правка не требовалась.

## 10. Оставшиеся шаги

1. Обновить `docs/IMPLEMENTATION_STATUS.md` и запись `## [Unreleased]` в
   `CHANGELOG.md`.
2. Фактически выполнить CLI quick start на распакованной выгрузке.
3. Открыть pull request и дождаться зелёного CI.
4. После merge закрыть #245 и обновить roadmap #157.
