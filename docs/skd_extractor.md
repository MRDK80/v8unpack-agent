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

Ошибка одного отчёта не прерывает обработку остальных.

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

`Template.bin` — v8-контейнер с переменным заголовком (8 или 24 байта в
зависимости от версии платформы) + UTF-8 BOM + XML схемы компоновки данных.
`extract_skd_queries()` определяет начало XML динамически — по BOM, а при
его отсутствии по `<?xml`.
