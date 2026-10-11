# Общий точный поиск и диспетчер смыслового fallback (#326)

Модуль `v8unpack_agent.search_dispatch` даёт единый маршрут поиска по формам и
модулям: точное разрешение идентификатора или имени, затем — только при
промахе — смысловой поиск через `query_index` (#325, #362). Результат —
`SearchResult` контракта `search_contract/1` с различимыми состояниями
`exact`, `ambiguous`, `semantic`, `empty` и `error`. Контракты
`search_contract/1`, `search_corpus/1`, `search_fragments/1`, формат снимка
`search_index/1`, `FormRouter`, `FormDispatcher` и `FormRagIndex` не меняются.

## API

```python
from v8unpack_agent.search_contract import SearchFilters, SearchQuery
from v8unpack_agent.search_dispatch import ExactCatalog, SearchDispatcher

dispatcher = SearchDispatcher(
    catalog=ExactCatalog.from_corpus(corpus),  # или from_fragmentation / from_artifacts
    index=index,                  # SearchIndex | None
    embedder=embedder,            # Embedder | None, передаётся явно
    fragmentation=fragmentation,  # FragmentationResult | None: fresh/stale или unverified
)
out = dispatcher.search(SearchQuery("Контрагенты"))
out.result              # SearchResult
out.path                # ("exact",) | ("exact", "semantic") | ("semantic",)
out.index_status        # IndexStatus | None
out.exact_filtered_out  # точные кандидаты, отсеянные фильтрами
```

- `ExactCatalog` — неизменяемый каталог артефактов; повтор артефакта
  схлопывается, разные описания одного `artifact_id` отклоняются. Конструкторы:
  `from_artifacts`, `from_documents`, `from_corpus`, `from_fragmentation`.
  Каталог берёт артефакты документов: артефакт без текста (исключённый
  корпусом #323) точным поиском не находится.
- `ExactCatalog.resolve(query)` — точное разрешение без эмбеддера
  (`ExactResolution`: попадания после фильтров и число отсеянных).
- `SearchDispatcher.search(query)` — маршрут по `query.mode`; возвращает
  `DispatchResult`. Корневой `v8unpack_agent.__init__` не расширяется.

## Точное разрешение

Запрос сравнивается целиком, после `strip()`, по `identity_key` (NFC и
`casefold`, #322). Порядок, первое совпадение выигрывает:

| Шаг | Поле | `method` / `matched_field` | Что находит |
|---|---|---|---|
| 1 | `artifact_id` | `exact_id` / `artifact_id` | модуль, форма или карточка владельца (`owner:…`) |
| 2 | `form_key[3]` | `exact_name` / `form_name` | формы с таким именем |
| 3 | `owner.owner_name` | `exact_name` / `owner_name` | модули и формы с установленным владельцем |

- Совпадение на шаге 1 завершает разрешение: имена не проверяются.
- Внутри одного артефакта `form_name` проверяется раньше `owner_name`.
- Карточка владельца находится только по `artifact_id`; по `owner_name` её нет.
- Формы без владельца (общие, внешние со связью `unconfirmed`) по
  `owner_name` не находятся — только по `form_name` или `artifact_id`.
  Неподтверждённая связь не становится доказанной.
- Имя команды — полное `owner_name` (`Контрагенты.Открыть`); короткое имя
  команды отдельным алиасом не является.
- Не алиасы: `form_key` без префикса `form:`, `document_id` с `#процедура`,
  запись `Тип.Имя`, синоним владельца, нормализация пробелов. Подстрока и
  отдельные слова запроса точным совпадением не считаются: «форма ФормаСписка
  контрагента» — свободный текст.

Одно попадание — `exact`, два и более — `ambiguous` с полным списком
кандидатов без ограничения `top_k`. Фильтры запроса применяются к кандидатам;
отсеянные считаются в `exact_filtered_out`.

## Маршрут

| `mode` | Точный шаг | Смысловой шаг |
|---|---|---|
| `auto` | всегда первым | только если после фильтров нет точных кандидатов |
| `exact` | единственный; промах — `empty` | не выполняется |
| `semantic` | не выполняется | `query_index` |

- `exact` и `ambiguous` возвращаются без вызова эмбеддера; при `ambiguous`
  fallback не выполняется.
- Fallback после промаха, в том числе когда точные кандидаты отсеяны
  фильтрами, вызывает `query_index` с тем же запросом и фильтрами.
- Отбор по артефакту выполняет `query_index` (#362); диспетчер его не повторяет.

## Ошибки, совместимость и свежесть

| Ситуация | `result` | `index_status` |
|---|---|---|
| Нет индекса или эмбеддера, смысловой шаг нужен | `error` / `index_not_ready` | `None` |
| `stale` или `incompatible` | `error` / `incompatible_index` (из `query_index`) | задан |
| Сбой эмбеддера | `error` / `embedder_failed` | задан |
| Неверный вектор | `error` / `invalid_vector` | задан |
| `compatible` + `unverified` | запрос выполняется | `unverified` виден потребителю |

`index_status` вычисляется `SearchIndex.assess()` без вызова `embed`.
Совместимость и свежесть остаются независимыми осями. Точный шаг не зависит от
индекса: устаревший или несовместимый снимок блокирует только смысловой шаг.
Автоматической пересборки, загрузки модели и сетевых обращений нет.

## Происхождение, порядок и дубликаты

- Выдача приходит из одного шага: `SearchResult` не смешивает точные и
  смысловые попадания (#322). Смысловой шаг запускается, только когда точных
  кандидатов после фильтров нет, поэтому артефакт не повторяется между путями.
- `exact`/`ambiguous` — по `identity_key(artifact_id)`, `semantic` — по
  убыванию `similarity`, при равенстве по артефакту (порядок контракта #322).
- `similarity` — оценка конкретного эмбеддера, не вероятность и не
  доказанная связь.
- Каталог, корпус, фрагменты, индекс и результаты не изменяются.

## Ограничения

- Хэшированный или fake-эмбеддер в тестах проверяет механику, не качество.
- eval-набор #321: запрос e4 «Загрузка Цен» ожидает `exact` для `ЗагрузкаЦен`
  через синоним «Загрузка цен». Синонима нет в `ModuleEntry` и контракте
  #322, нормализация пробелов не применяется, поэтому e4 даёт точный промах и
  смысловой fallback. Расхождение учитывается в #329, eval-наборы не менялись.
- Сборка контекста (#327) и командный сценарий (#328) не входят.
