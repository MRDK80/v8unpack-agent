# v8unpack-agent

Прикладной инструмент над upstream-распаковщиком `v8unpack`: превращает
выгрузку конфигурации 1С в читаемый текстовый слой, машиночитаемую опись
форм и компактный контекст для LLM.

Задача пакета — дать агенту достоверные сведения о формах и модулях без
угадывания. Где данные нечитаемы, результат помечается как неполный, а не
достраивается догадками.

## Границы и non-goals

- Пакет не распаковывает контейнер сам: дерево выгрузки готовит upstream
  `v8unpack`, а агент работает с уже раскрытым каталогом.
- Production-адаптера одиночного `Form.bin` в пакете нет: реализацию
  распаковщика передаёт вызывающая сторона.
- Функций `index_cf()` и `rag.rebuild()` не существует; векторная индексация
  находится вне scope пакета.
- Пакет не подключается к живой информационной базе и не читает её данные.

## Кто что решает

| Компонент | Ответственность |
|---|---|
| upstream `v8unpack` | распаковка контейнеров в дерево файлов |
| `v8unpack-agent` | опись форм, текстовый слой, индексы, контекст, отчёт о запуске |
| вызывающая сторона | реализация распаковщика формы и решения по degraded-результату |

## Установка

Текущий способ — установка из Git. Публикация в индексе пакетов отслеживается
в issue #149 и пока не выполнена.

```bash
pip install "v8unpack>=1.2.9"
pip install git+https://github.com/MRDK80/v8unpack-agent.git
```

Установка для разработки из клона:

```bash
pip install -e ".[test]"
```

upstream `v8unpack` нужен для фактического извлечения текстов, включая
поддержку внешних отчётов `.erf`.

## Быстрый старт: CLI

Один прогон — один отчёт и один код возврата. На вход подаётся каталог
уже распакованной выгрузки.

```bash
v8unpack-agent-run <корень_выгрузки> --report-path post-run.json
```

Аргумент `--report-path` обязателен, значения по умолчанию нет, а
каталог-родитель должен существовать заранее.

| Код | Значение |
|---:|---|
| 0 | все объекты обработаны полностью |
| 2 | ошибка аргументов или корень не является каталогом |
| 3 | degraded: есть частичные или отказавшие объекты |
| 4 | управляемая фатальная ошибка пайплайна |
| 5 | ошибка записи отчёта |
| 6 | фатальная ошибка и ошибка записи одновременно |

Degraded — неуспешное завершение: неполный контекст не должен выглядеть
успехом для автоматизации. Полное описание аргументов, стадий и кодов причин —
в [`docs/runner.md`](docs/runner.md), схема отчёта — в
[`docs/run_report.md`](docs/run_report.md).

## Быстрый старт: Python

Каноническая цепочка: обнаружение источников, распаковка по `FormBinSource`,
обновление индекса.

```python
from pathlib import Path

from v8unpack_agent import unpack_all_forms, update_forms_index
from v8unpack_agent.form_artifact import FormArtifact
from v8unpack_agent.form_identity import FormBinSource
from v8unpack_agent.scan_forms import scan_forms

dump_root = Path("unpacked_cf")
unpacked_root = Path("text_layer")


def unpack_one(source: FormBinSource, root: Path) -> FormArtifact:
    """Здесь вызывается реальное извлечение текстов из source.bin_path."""
    return FormArtifact.for_form(root, source.name)


scan_index = scan_forms(dump_root)
print(f"Найдено форм: {scan_index.total}")

artifacts = unpack_all_forms(dump_root, unpacked_root, unpack_one)
index = update_forms_index(dump_root, unpacked_root, artifacts)
index.save(Path("forms_index.json"))
```

Распаковщик получает `FormBinSource` и корень текстового слоя — ровно два
аргумента. Существующая трёхаргументная реализация подключается только через
`adapt_legacy_unpacker()`. Подробный разбор и отбор по `form_ids` — в
[`docs/pipeline.md`](docs/pipeline.md), готовые сценарии — в
[`examples/README.md`](examples/README.md).

## Пайплайн

| Шаг | Функция | Результат |
|---|---|---|
| опись форм | `scan_forms()` | `FormScanIndex` с layout-метаданными и индексом ссылочных типов |
| обнаружение источников | `discover_form_sources()` | `FormBinSource` с каноническим `form_id` |
| распаковка | `unpack_all_forms()` | `FormArtifact` на каждую форму |
| структура формы | `parse_elem_json()` | элементы и привязки `data_path`, best-effort |
| реквизиты объекта | `decode_object_attributes()` | `Properties` и `TabularSections` |
| классификация | `classify_form()` | `object`, `service` или `unknown` |
| внешние отчёты | `unpack_erf()` | текстовый слой и запросы СКД |
| индекс актуальности | `update_forms_index()` | `FormsIndex` с ключом `form_id` |
| контроль дрейфа | `check_drift()` | `DriftReport` по хешам |
| контекст для LLM | `build_form_context()` | `FormContext` и промпт-фрагмент |

## Ключевые возможности

- Одноимённые формы разных владельцев не затирают друг друга: идентичность —
  `form_id`, а не имя.
- Формы без кода попадают в опись: elem-only ветка заполняет `elem_json_path`.
- Частичный результат всегда явный: `extraction_ok=False` невозможен без
  непустого `extraction_warnings`.
- Ссылочные типы реквизитов приводятся к имени объекта метаданных; неизвестный
  UUID остаётся `Ref#<uuid>` и не угадывается.
- Сервисные формы отделены от объектных, чтобы метрика покрытия не занижалась
  архитектурным паттерном платформы.
- Дрейф детектируется по хешам модуля и структуры, а не только по времени
  изменения файла.
- Сериализация индексов переносима: в файлы пишутся только относительные
POSIX-пути, одинаковые на POSIX и NT.

## Версии схем

Три независимые версии, которые не сравниваются друг с другом.

| Артефакт | Версия | Где описан |
|---|---:|---|
| `FormsIndex` | 2 | [`docs/pipeline.md`](docs/pipeline.md) |
| `FormScanIndex` | 2 при чтении legacy 1 | [`docs/scan_forms.md`](docs/scan_forms.md) |
| post-run report | 1 | [`docs/run_report.md`](docs/run_report.md) |

## Публичная поверхность

| Модуль | Основные имена | Канонический документ |
|---|---|---|
| `scan_forms` | `scan_forms`, `FormEntry`, `FormScanIndex`, `scan_warning_code` | [scan_forms](docs/scan_forms.md) |
| `pipeline` | `unpack_all_forms`, `unpack_erf`, `update_forms_index`, `FormUnpacker` | [pipeline](docs/pipeline.md) |
| `form_identity` | `FormBinSource`, `discover_form_sources`, `select_sources`, `adapt_legacy_unpacker` | [pipeline](docs/pipeline.md) |
| `form_artifact` | `FormArtifact` | [pipeline](docs/pipeline.md) |
| `forms_index` | `FormsIndex`, `FormsIndexEntry`, `is_form_stale` | [pipeline](docs/pipeline.md) |
| `runner`, `cli` | `RunOptions`, `RunOutcome`, `run_pipeline` | [runner](docs/runner.md) |
| `run_report` | `PostRunReport`, `ObjectRunResult`, `write_post_run_report` | [run_report](docs/run_report.md) |
| `elem_parser` | `parse_elem_json`, `ElemIndexResult`, `UnindexedReason` | [elem_parser](docs/elem_parser.md) |
| `form_classifier` | `classify_form`, `FormClass`, `classify_empty_tree_form` | [form_classifier](docs/form_classifier.md) |
| `coverage_metric` | `calc_data_path_coverage`, `CoverageReport` | [form_classifier](docs/form_classifier.md) |
| `object_decoder` | `decode_object_attributes`, `DecodeResult`, `DecodeError` | [object_decoder](docs/object_decoder.md) |
| `catalog_resolver` | `resolve_data_path`, `ResolvedBinding` | [catalog_resolver](docs/catalog_resolver.md) |
| `form_context` | `FormContext`, `build_form_context`, `to_llm_prompt_fragment` | [form_context](docs/form_context.md) |
| `form_summary` | `build_form_summary`, `to_normalized_json` | [form_summary](docs/form_summary.md) |
| `form_router` | `FormRouter`, `form_paths` | [form_router](docs/form_router.md) |
| `common_modules` | `scan_common_modules`, `build_common_module_context` | [common_modules](docs/common_modules.md) |
| `managed_forms` | `discover_elem_forms`, `ElemFormEntry` | [managed_forms_structure](docs/managed_forms_structure.md) |
| `skd_extractor` | `extract_skd_queries`, `extract_all_skd_queries` | [skd_extractor](docs/skd_extractor.md) |
| `drift_checker` | `check_drift`, `DriftReport` | [drift_checker](docs/drift_checker.md) |

## Документация

### Начало работы

- [Текущее состояние реализации](docs/IMPLEMENTATION_STATUS.md)
- [Процесс контрибуции](CONTRIBUTING.md)
- [История изменений](CHANGELOG.md)

### Пайплайн и запуск

- [Контракт пайплайна и `FormUnpacker`](docs/pipeline.md)
- [Production runner, CLI и коды возврата](docs/runner.md)
- [Схема отчёта о запуске](docs/run_report.md)

### Формы и разбор

- [Структура элементов и `UnindexedReason`](docs/elem_parser.md)
- [Классификация форм и метрика покрытия](docs/form_classifier.md)
- [Сводка по форме](docs/form_summary.md)
- [Контекст для LLM](docs/form_context.md)
- [Маршрутизация путей формы](docs/form_router.md)
- [Структура управляемых форм](docs/managed_forms_structure.md)
- [Структура внешних обработок и отчётов](docs/external_forms_structure.md)
- [Запросы СКД](docs/skd_extractor.md)

### Метаданные и индексы

- [Опись форм и индекс ссылочных типов](docs/scan_forms.md)
- [Реквизиты объекта из raw-секции `header`](docs/object_decoder.md)
- [Разрешение `data_path`](docs/catalog_resolver.md)
- [Общие модули](docs/common_modules.md)
- [Контроль дрейфа](docs/drift_checker.md)

### Исследования

Датированные протоколы с агрегатами; выводы не переписываются задним числом.

- [Доля неиндексированных форм](docs/research/unindexed_share_issue229.md)
- [Разрешение ссылочных типов](docs/research/ref_resolver_issue143.md)
- [Нессылочные типовые грани](docs/research/non_reference_type_facets_issue166.md)
- [Кросс-конфигурационная проверка типов](docs/research/platform_types_cross_config_issue164.md)
- [Пустые реквизиты объекта](docs/research/missing_object_attributes_issue163.md)
- [Структура `Form.bin`](docs/research/form_bin_issue150.md)
- [Валидация на третьей выгрузке](docs/research/third_configuration_validation.md)

### Примеры и политики

- [Исполняемые примеры](examples/README.md)
- [Политика ветвления](docs/branch_protection.md)
- [Аудит документации](docs/documentation_audit_issue245.md)

## Известные ограничения

- Обычные (неуправляемые) формы хранят разметку в бинарном виде и частично
  не читаются; такие формы получают `unknown` и исключаются из знаменателя
  метрики.
- Квалификаторы реквизитов и составные типы не декодируются.
- Индекс ссылочных типов строится только для видов метаданных с доказанной
  ссылочной формой; остальные UUID остаются `Ref#<uuid>`.
- В режиме `mode="external"` индекс ссылочных типов не собирается.
- Распаковщики из `examples/` и тестов — заглушки и не читают бинарный
  формат.
- Пакет ещё не опубликован в индексе пакетов (issue #149).

## Тесты

```bash
pytest
```

Фикстуры синтетические: реальная выгрузка для прогона не требуется. Порядок
работы с ветками, проверки перед PR и требования к обезличенности описаны в
[CONTRIBUTING.md](CONTRIBUTING.md).

## Связанное

- [`saby-integration/v8unpack`](https://github.com/saby-integration/v8unpack) —
  upstream-распаковщик на Python, лицензия MIT.

## Лицензия

MIT — см. [LICENSE](LICENSE).
