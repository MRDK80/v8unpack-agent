# Контроль дрейфа (drift_checker)

`check_drift()` сравнивает текущее состояние выгрузки на диске с ранее
сохранённым `forms_scan_index.json` и возвращает `DriftReport` — отчёт о
расхождениях.

```python
from pathlib import Path
from v8unpack_agent import check_drift

report = check_drift(
    cf_export_root=Path("/path/to/cf_export"),
    index_path=Path("forms_scan_index.json"),
    save_to=Path("drift_report.json"),   # опционально
)

if report.has_drift:
    print("Добавлены:",          report.added)
    print("Удалены:  ",          report.removed)
    print("Изменены (код): ",    report.modified)
    print("Изменены (разм.):",   report.structure_modified)
    print("Stale BSL:",          report.stale_extractions)
else:
    print("Дрейфа нет, индекс актуален")
```

## DriftReport

| Поле | Тип | Значение |
|---|---|---|
| `added` | list[str] | Ключи форм, появившихся на диске после последнего сканирования |
| `removed` | list[str] | Ключи форм, исчезнувших с диска (были в индексе). Elem-only формы не включаются — у них нет BSL по дизайну (issue #58) |
| `modified` | list[str] | Ключи форм с изменившимся содержимым BSL-файла (код формы). При наличии `bsl_sha256` в baseline — hash-based; при отсутствии (старый индекс) — legacy fallback через `bsl_mtime` с допуском 1 сек. (issue #38). Elem-only формы не включаются |
| `structure_modified` | list[str] | Ключи форм с изменившимся деревом элементов (issue #40, #58). Включает **как обычные, так и elem-only формы** при наличии `elem_sha256` в baseline. Независимый сигнал от `modified` |
| `stale_extractions` | list[str] | Формы из индекса, чей `bsl_path` не существует на диске. **Elem-only формы исключены** — отсутствие BSL у них не признак stale (issue #58) |
| `has_drift` | bool | `True` если хотя бы одно из полей непусто |
| `checked_at` | str | ISO 8601 метка времени проверки |

**Ключ формы** имеет вид `"ObjectType/ObjectName/ContainerName/FormName"`.
Для CommonForm: `"CommonForm//CommonForm/ФормаИмя"`.

## Алгоритм детекции

### modified (код формы, issue #38)

- Если baseline-запись содержит `bsl_sha256`: пересчитывается SHA-256 текущего
  `bsl_path`, сравнивается с сохранённым. Изменение только `mtime` при
  неизменном содержимом **не** помечает форму как `modified`.
- Если `bsl_sha256 = null` (старый индекс): legacy fallback — сравнение
  `bsl_mtime` с допуском 1 сек.
- Elem-only формы (без `.obj.bsl`) **не участвуют** в `modified` / `removed` /
  `stale_extractions` — только в `structure_modified`.

### structure_modified (разметка формы, issue #40, #58)

- Если baseline-запись содержит `elem_sha256`: пересчитывается хэш
  нормализованного `form_elements_index` (только структурно значимые поля;
  косметика — координаты, цвета, шрифты, GUID — исключена), сравнивается
  с сохранённым.
- Если `elem_sha256 = null` (старый индекс или файл не найден): сигнал
  тихо пропускается, false-positive не порождается.
- **Elem-only формы** (без `.obj.bsl`) включены: кандидаты берутся из
  `index_elem` напрямую, пересканирование — с `include_elem_only=True`.

### elem-only формы (issue #57, #58)

Формы без `.obj.bsl` (управляемые формы конфигураций смешанного типа)
определяются по признаку: `elem_json_path` заполнен И `bsl_path` не существует.

- **Не порождают** `removed` / `stale_extractions` — отсутствие BSL штатно.
- **Участвуют** в `structure_modified` при наличии `elem_sha256` в baseline.
- На живых данных (конфигурация УТ 10.3): 49 elem-only форм из 2 216 всего.

## Типичные сценарии

| Действие | `modified` | `structure_modified` |
|---|---|---|
| Правка кода формы (BSL), разметка не тронута | ✓ | — |
| Добавление/удаление элемента на форме, BSL не тронут | — | ✓ |
| Одновременная правка кода и разметки | ✓ | ✓ |
| Косметика формы (координаты, цвета) без смысловых изменений | — | — |
| Повторная полная распаковка неизменённого `.cf` | — | — |
| Elem-only форма, разметка изменилась | — | ✓ |
| Elem-only форма, разметка не тронута | — | — |

## Поведение при отсутствии индекса

Если `index_path` не найден — `added` содержит все формы на диске,
`has_drift=True`. Исключение не бросается — это штатная ситуация первого запуска.

## Сохранение и загрузка отчёта

```python
from v8unpack_agent import DriftReport

report = DriftReport.load_from(Path("drift_report.json"))
print(report.checked_at, report.has_drift)
```

## Известные ограничения

- **`mode=external` не поддерживается** в `check_drift()` — `_disk_snapshot`
  реализован только для config-layout. При использовании с external-выгрузкой
  все формы ложно уходят в `removed`. Отслеживается в issue #73.
