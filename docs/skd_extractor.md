# Внешние отчёты (.erf) и СКД (skd_extractor)

`.erf`-файлы распаковываются в два шага:

```
my_report.erf
  └─► unpack_erf() / v8unpack.extract() → текстовый слой
       └─► extract_skd_queries()         → skd_queries.json
```

```python
from pathlib import Path
from v8unpack_agent import unpack_erf, extract_skd_queries

result = unpack_erf(Path("my_report.erf"), Path("text_layer/my_report"))
skd    = extract_skd_queries(Path("text_layer/my_report"))

print(skd.skd_extracted)   # True
for ds in skd.datasets:
    print(ds["name"], "|", ds["query"][:60])
```

Если `skd_extracted=False` — агент видит только BSL модуля отчёта. Это не
ошибка пайплайна, а сигнал о неполноте контекста (отчёт без СКД или
нераспознанный формат `Template.bin`).

## Пакетный режим

Если под корнем несколько отчётов — `extract_skd_queries()` берёт только
первый `Template.bin`. Для обхода всех используй пакетный вариант:

```python
from pathlib import Path
from v8unpack_agent import extract_all_skd_queries, SkdBatchResult

batch: SkdBatchResult = extract_all_skd_queries(Path("text_layer/Report"))

print(batch.skd_extracted)       # True если хотя бы один отчёт извлёкся
for result in batch.results:
    if result.skd_extracted:
        print(result.datasets[0]["name"], "|", result.datasets[0]["query"][:60])
print(batch.warnings)            # предупреждения по неудачным отчётам
```

Ошибка одного отчёта не прерывает обработку остальных. Каждый
`Template.bin` разбирается отдельно, в том числе когда у объекта несколько
макетов; `skd_queries.json` пишется один на корень объекта и объединяет
запросы всех его макетов.

## Запись `skd_queries.json` (issue #335)

По умолчанию `extract_skd_queries()` и `extract_all_skd_queries()` пишут
`skd_queries.json` рядом со входом. С `write_json=False` файлы не создаются и
не изменяются, результат доступен только в возвращаемом объекте. Шаг 10
`examples/full_pipeline_walkthrough.py` вызывает пакетный режим с
`write_json=False` и не изменяет выгрузку.

## Статусы результата (issue #287)

`SkdResult.status` объясняет, почему запроса нет. `skd_extracted` и
`datasets` сохраняют прежний смысл: `True` только при извлечённом тексте.

| `status` | Значение | Сбой? |
|---|---|---|
| `query_extracted` | текст запроса извлечён | нет |
| `schema_without_query` | валидная СКД, наборы без текста запроса (из объекта, объединение) | нет |
| `schema_without_datasets` | СКД без наборов данных | нет |
| `not_schema` | макет другого вида (не СКД) | нет |
| `error` | ошибка чтения/разбора или нераспознанный случай, в том числе тег запроса без распознанного текста | да |

`SkdResult.template_path` — путь к разобранному `Template.bin`. Шаг 10
`examples/full_pipeline_walkthrough.py` считает `degraded` только при
`error`.

## Конвенция путей (внешние отчёты)

```
<unpacked_root>/
  ExternalDataProcessor.obj.bsl                  # BSL модуль отчёта
  Template/
    ОсновнаяСхемаКомпоновкиДанных/
      Template.bin                               # v8-контейнер с XML СКД
<unpacked_root>.parent/
  skd_queries.json                               # извлечённые запросы СКД
```

## Формат Template.bin

`Template.bin` — v8-контейнер с двоичным заголовком переменной длины
(встречается от 8 до 48 байт, бывает и без него) + UTF-8 BOM + XML схемы
компоновки данных с корнем `SchemaFile` или `DataCompositionSchema`.
`extract_skd_queries()` определяет начало XML динамически — по BOM, а при
его отсутствии по `<?xml`.
