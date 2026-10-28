# Общий поиск: библиотека и opt-in CLI (#328)

Вход: `python -m v8unpack_agent.search_cli {build,query}`.
Это отдельный модуль: `v8unpack-agent-run`, его parser, defaults и exit codes
не меняются; console script поиска не добавлен. Поиск входит в `main` и
опубликован в релизе `0.2.0` (#393); в `0.1.0` его нет.

## Примеры

Пример на реальной выгрузке — `examples/search_export.py` (#404): тот же
контур через `search_api` от каталога выгрузки до контекста, с обезличенным
агрегатом и проверкой неизменности выгрузки.

## Офлайн-пример

```bash
pip install -e ".[test]"
python examples/unified_search.py
```

Корпус в памяти: две формы и модули object/manager/record_set/common_module.
Сценарии: exact, ambiguous, semantic, фильтр, бюджет, отсутствие эмбеддера,
сохранение и загрузка снимка. Это не scanner и не проверка layout выгрузки.
Внешние данные, платформа 1С и сеть не нужны; снимки удаляются.
Самопроверка: RC=0 при успехе, RC=1 при расхождении; детерминизм — тест #251.

## Аргументы и defaults

export_root — каталог уже распакованной выгрузки, не контейнер `.cf`.
Пример выше не создаёт выгрузку для CLI. Help:

```bash
python -m v8unpack_agent.search_cli build --help
python -m v8unpack_agent.search_cli query --help
```

build: обязательны export_root, `--snapshot PATH`,
`--embedder hashed-bow-demo`. Корпус → fragment_corpus → построение и
атомарное сохранение снимка. Каталог-родитель должен существовать.
`--snapshot-format 1|2` (default 1): `1` — `search_index/1`, один JSON;
`2` — `search_index/2` (#413), метаданные по PATH и файл векторов
`PATH.<hash>.vec` рядом. `query --snapshot` читает оба формата;
`snapshot.schema` в JSON build — схема записанного снимка.
Без --embedder — код 2, не скрытый выбор модели.

query: обязательны export_root и `--query TEXT`.

| Аргумент | Default / действие |
|---|---|
| --snapshot PATH | не задан; загрузить снимок |
| --embedder hashed-bow-demo | не задан; эмбеддер только явно |
| --search-mode | auto; варианты auto, exact, semantic |
| --top-k N | 5; положительное число смысловых кандидатов |
| --max-chars N | -1 — без ограничения; допустимо N >= 0 |
| --include-text | выключен; исходный текст только явно |
| --artifact-kind VALUE | повторяемый; form, module, owner |
| --module-kind VALUE | повторяемый фильтр вида модуля |
| --owner-kind VALUE | повторяемый фильтр вида владельца |
| --metadata-type VALUE | повторяемый фильтр типа метаданных |
| --owner-name VALUE | повторяемый фильтр имени |

Фильтры передаются в SearchFilters. Неверные значения, пустой запрос,
управляющие символы, top_k < 1 или max_chars < -1 — код 2 без JSON.

Общие флаги корпуса: `--mode config|external` (default config),
`--skip-common-modules` (выключен), повторяемые --module-group
или --skip-module-group. Группы: configuration, metadata, record-sets,
commands, value-managers, services; default — все. Include и skip вместе,
неизвестная группа или исключение всех — код 2. Индекс модулей всегда
включён: --include-module-index не нужен. В external дополнительно
собираются модули внешних объектов независимо от групповых флагов (#351).

## Поиск, эмбеддер, снимок

- exact: весь запрос разрешается по id, затем имени формы/владельца;
  подстрока не совпадение. Промах — empty, не ошибка.
- auto: смысловой fallback только после точного промаха. ambiguous
  возвращает альтернативы без fallback и без ограничения top-k.
- semantic: явно запрошенные смысловые кандидаты. Фильтры — до отбора;
  до top_k разных артефактов по лучшему фрагменту (#362).

Без индекса или эмбеддера смысловой шаг — error/index_not_ready;
точный поиск работает. Similarity не является вероятностью правильного ответа.
`hashed-bow-demo` — механика, не качество поиска: детерминированный
хэшированный мешок слов, 256 измерений. Идентичность —
v8unpack-agent-hashed-bow-demo, версия 1. Пользовательский код,
провайдер, сеть и загрузка модели не подключаются.

query пересобирает корпус и фрагментацию. Снимок хранит векторы, координаты
и хэши, не текст; контекст берётся из актуальных фрагментов. При загруженном
снимке и заданном эмбеддере index.assess вызывается даже для exact:
compatibility и freshness независимы. Без эмбеддера оба — null, не
доказательство свежести. Проверка CLI с фрагментацией даёт fresh/stale,
не unverified. Сбой снимка не прерывает exact, но общий результат degraded,
код 3. incompatible/stale блокируют semantic.

Размещайте снимок вне выгрузки: сбор корпуса читает вход, но build --snapshot
явно пишет в выбранное место. Запрет размещения внутри выгрузки не вводился
(#375). Прямой parse_elem_json по-прежнему пишет индекс по умолчанию;
не переносите на него read-only гарантию поиска.

## stdout: JSON search_cli/1

Один канонический JSON: сортированные ключи, UTF-8, LF в конце.
Ошибка аргументов/корня — без JSON; --help выводит help.
Несовместимое изменение полей потребует search_cli/2.

Общие поля: schema=search_cli/1, command=build или query,
status=ok/degraded/error, error=null либо {code,message}.

| Команда | Дополнительные поля |
|---|---|
| build | corpus: {total,counts}, fragments, snapshot: {schema,representation_version,embedder,entries} |
| query | snapshot: {provided,loaded,error_code,compatibility,freshness}, context |

corpus.total считает строки отчёта, включая исключённые. fragments/entries —
числа фрагментов/записей. embedder: {name,version,dimension,config_hash}.
Ранний сбой оставляет невычисленное null; сбой корпуса query —
snapshot=null, context=null.

Для query provided/loaded — boolean; error_code — null, snapshot_not_found
или код load_index, например corrupt_snapshot; compatibility —
null/compatible/incompatible; freshness — null/fresh/stale.
context равен SearchContext.to_dict(include_text=...): dispatch с path,
index_status, exact_filtered_out, сведения о кандидатах, включениях,
warnings и omitted. Поиск и сборка контекста имеют независимые состояния.
Подробности: [search_context](search_context.md),
[search_dispatch](search_dispatch.md), [search_contract](search_contract.md).

status=degraded может иметь error=null: причина в snapshot.* или
context.status. Проверяйте их и exit code. На одном корпусе, снимке и
параметрах поле context совпадает с библиотекой.
Абсолютных путей и traceback нет; относительные источники и имена не
обещают обезличенности реальной конфигурации. Текст — только с
--include-text; разрешение на внешнюю передачу принимает потребитель.

## stderr и exit codes

stderr: одна санитизированная строка, без traceback и текста исключения.
Аргументы: «ошибка:» без usage; корень: «корень выгрузки не найден или не
является каталогом». При успехе stderr пуст. Сводка неуспешного JSON:
`python -m v8unpack_agent.search_cli: STATUS: REASONS`.
Причины: код стадии/поиска, snapshot:CODE, snapshot:incompatible,
snapshot:stale, context:STATUS. Внутренний fail-closed обработчик пишет
фиксированный internal_error и класс исключения, не str(exc).

| Код | status | Условие |
|---:|---|---|
| 0 | ok | exact/ambiguous/empty/semantic; built/truncated; без замечаний к снимку |
| 2 | без JSON | аргументы/валидация; export_root не каталог |
| 3 | degraded | index_not_ready/incompatible_index; сбой, incompatible/stale снимок; insufficient_budget |
| 4 | error | embedder_failed/invalid_vector/internal; scan_failed/common_modules_failed/modules_failed; ошибка build или внутренняя ошибка |
| 5 | error | snapshot_write_failed при build |

Коды — константы cli.py, не контракт runner с кодом 6. main(argv) возвращает
число, python -m завершает процесс. Help — 0. Токенного бюджета в CLI нет:
token_count_failed — только library. Фатальная ошибка поиска важнее снимка.
Фиксированные сообщения стадий в error.message: scan_failed — form scan failed;
common_modules_failed — common module discovery failed; modules_failed —
module discovery failed; embedder_failed при build — embedder failed;
invalid_vector при build — embedder returned an invalid vector;
snapshot_write_failed — snapshot could not be written; internal_error —
internal error. Для ошибки поиска message берётся из SearchError.message,
не из build только из-за одинакового кода.

Library: прямой импорт v8unpack_agent.search_api; max_chars в
search_with_context обязателен, эмбеддер передаётся явно. Пример строит
SearchCorpus публичными типами в памяти, не scanner. Схемы search_*/1
независимы; это не реализация контрактов #254/#258/#259.
