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
# result.source     — "router" (значение по умолчанию, #79)

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

## Двухуровневая маршрутизация (FormDispatcher)

`FormDispatcher` (#79, #308) объединяет `FormRouter` и необязательный
`FormRagIndex` ([form_rag](form_rag.md)). Роутер вызывается всегда и
первым; RAG — только если `route()` вернул пустой `matched`. Любой
непустой `matched` роутера считается попаданием независимо от `confidence`.

```python
from v8unpack_agent import FormDispatcher

# router — FormRouter, index — готовый FormRagIndex (см. form_rag.md)
dispatcher = FormDispatcher(router, rag=index)   # rag=None — только роутер
results = dispatcher.dispatch("список документов", top_k=5)
for result in results:
    print(result.source, result.confidence, result.matched)
```

| Ситуация | Результат `dispatch()` |
|---|---|
| `matched` роутера непуст | `[result]` — тот же объект, что вернул `route()`, `source="router"`; RAG не вызывается |
| промах, `rag=None` | `[result]` промаха роутера: пустой `matched`, `source="router"`, без исключения |
| промах, RAG подключён | до `top_k` новых `RouteResult` с `source="rag"` в порядке `rag.query(query, top_k)`; пустая выдача RAG — `[]` |
| исключение RAG | пробрасывается без перехвата, например `RagQueryError` при `top_k <= 0` |

`RouteResult.source` — строка со значением по умолчанию `"router"`, поэтому
код, создающий `RouteResult` без этого поля, работает как раньше. Объекты
из `rag.query()` диспетчер не изменяет (#308): он возвращает новые
`RouteResult`, списки `matched` и `warnings` копируются поверхностно,
элементы `FormEntry` общие с исходной выдачей.

Ограничения: `FormDispatcher` — библиотечный класс, в CLI, `runner` и
pipeline он не встроен. Слияния и переранжирования выдачи роутера и RAG нет.
Эмбеддер передаёт вызывающая сторона; LLM-провайдера пакет не содержит,
релевантность смысловой выдачи не гарантируется.
