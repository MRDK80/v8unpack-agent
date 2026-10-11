# Общий смысловой индекс и снимок (#325)

Модуль `v8unpack_agent.search_index` строит провайдер-независимый индекс над
`FragmentationResult` (#324), сохраняет его снимком и проверяет совместимость
и свежесть. Эмбеддер передаётся явно; сеть, ChromaDB и автоматическая
пересборка не используются. Контракты `search_contract/1`, `search_corpus/1`
и `search_fragments/1` не меняются.

## API

- `EmbedderIdentity(name, version, dimension, config_hash)` задаётся вызывающей
  стороной; `config_hash` — непрозрачная строка.
- `build_index(result, embedder)` — полная пересборка. Вектор отклоняется при
  неверной размерности, `NaN`, бесконечности, `bool`, нулевом векторе или
  неверном числе векторов (`SearchIndexError`, код `invalid_vector`); сбой
  эмбеддера даёт `embedder_failed` без текста исходного исключения.
- `save_index(index, path)` — временный файл рядом и одна замена `os.replace`;
  при сбое прежний снимок остаётся без изменений.
- `load_index(path)` — проверка структуры, контрольной суммы и каждого вектора;
  повреждение даёт `SearchIndexError("corrupt_snapshot")`.
- `query_index(index, query, embedder, fragmentation=None)` — фильтры
  применяются до top-k; равные оценки упорядочены по `document_id`. Выдача на
  уровне документа; агрегация по артефакту остаётся за #326.

## Политика хранения

Исходный текст и поисковое представление в снимке не хранятся. Запись содержит
`document_id`, артефакт, процедуру, координаты, три хэша (`text_sha256`,
`search_sha256`, `source_sha256`) и вектор. Снимок хранит `representation_version`
из `FragmentationResult`, схему корпуса `search_corpus/1`, схему снимка
`search_index/1` и идентичность эмбеддера.

## Статусы

Совместимость и свежесть — две независимые оси (`SearchIndex.assess`).

| Ось | Значения | Что сверяется |
|---|---|---|
| Совместимость | `compatible`, `incompatible` | схемы, `EmbedderIdentity`, `representation_version` (если передан корпус) |
| Свежесть | `fresh`, `stale`, `unverified` | три хэша и состав `document_id` с переданным корпусом |

Без корпуса свежесть — `unverified`: запросы допустимы, статус не выдаётся за
доказанную свежесть. `incompatible` и `stale` блокируют запросы: результат со
статусом `error` и кодом `incompatible_index`. Пересборка — только явным
вызовом `build_index`.
