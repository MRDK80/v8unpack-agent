# Маршрутизация агента (FormRouter)

`FormRouter` — инструмент для LLM-агента: LLM извлекает имя объекта или формы
из запроса пользователя и передаёт его в `route()`. Роутер не делает
LLM-вызовов — только строковое сопоставление.

```python
from pathlib import Path
from v8unpack_agent.form_router import FormRouter

router = FormRouter(index_path=Path("forms_scan_index.json"))

result = router.route("Банки")
# result.matched    — список FormEntry с путями к .bsl и .json
# result.confidence — 0.0–1.0
# result.warnings   — при нулевом результате

for entry in result.matched:
    print(entry.object_type, entry.object_name, entry.form_name)
    print(entry.bsl_path)
```

## Приоритет совпадений

| Уровень | Поле | Тип | conf |
|---|---|---|---|
| 1 | `form_name` | точное | 1.0 |
| 2 | `object_name` | точное, case-insensitive | 0.9 |
| 3 | `object_type` | частичное, case-insensitive | 0.4 |

Сравнение на уровнях 2–3 регистронезависимо: `"банки"`, `"Банки"` и `"БАНКИ"`
найдут `Catalog/Банки`.

## Внешние обработки и отчёты

`FormRouter` работает поверх единого `FormScanIndex`, поэтому формы внешних
обработок и отчётов (`mode="external"`) маршрутизируются тем же `route()`
без отдельного API.

```python
# индекс собран из внешних обработок/отчётов
router = FormRouter(index_path=Path("forms_scan_index.json"))

result = router.route("ExternalReport")  # по object_type → conf 0.4
result = router.route("ЗагрузкаЦен")    # по object_name  → conf 0.9
```

`object_type` external-объектов (`ExternalDataProcessor` / `ExternalReport`)
не пересекается с типами конфигурации, поэтому коллизий в смешанном индексе
(конфигурация + External) нет. При неоднозначности приоритет отдаётся
`form_name` (1.0) над `object_name` (0.9).

## Инкрементальное обновление

```python
router.reindex([updated_entry])   # обновляет без полного пересканирования
```
