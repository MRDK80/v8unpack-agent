# form_rag — RAG-индекс форм

`v8unpack_agent.form_rag.FormRagIndex` строит векторный индекс форм поверх
`FormScanIndex` и отвечает на текстовые запросы списком `RouteResult`.
Модуль появился в #78 (формат D1), контракт D2 и защитные проверки добавлены
в #305.

## Контракт

```python
from v8unpack_agent.form_rag import FormRagIndex

index = FormRagIndex(scan_index)            # FormScanIndex — источник FormEntry
index.build(contexts, embedder)             # list[FormContext], str -> list[float]
results = index.query("текст", top_k=5)     # list[RouteResult]
index.save(index_dir)

restored = FormRagIndex(scan_index)
restored.load(index_dir, embedder)          # эмбеддер передаётся явно
```

- Идентичность формы — 4-кортеж
  `(object_type, object_name, container_name, form_name)`, тот же, что
  в `FormRouter.reindex`. `query()` возвращает `FormEntry` из переданного
  `scan_index`; публичных методов в `FormScanIndex` не добавлено.
- Один `RouteResult` на форму: `matched=[entry]`,
  `confidence` — cosine similarity, обрезанная в `[0, 1]`. Сортировка по
  убыванию `confidence`, при равенстве — по ключу формы.
- `RouteResult.source` здесь не заполняется: это поле появится в #79.

### Эмбеддер после load()

Эмбеддер — функция, а не данные, поэтому на диск он не пишется. `load()`
принимает его обязательным вторым аргументом: `load(index_dir, embedder)`.
Это самый короткий явный способ: без переменных окружения, без реестра
провайдеров и без скрытого глобального состояния. Размерность векторов
эмбеддера сверяется с индексом при каждом `query()`.

## Provider-neutral injection

- `FormRagIndex` не знает о провайдерах эмбеддингов, не читает переменные
  окружения, не импортирует сетевые библиотеки и сам не выполняет сетевых
  вызовов.
- Эмбеддер создаёт вызывающая сторона и передаёт в `build()`/`load()`.
  Если эмбеддер ходит в сеть, это решение и ответственность вызывающего кода.
- Текст для эмбеддинга — только `to_llm_prompt_fragment(context, ...)`.
  Санитизация диагностики (#142) и строки отрицательного знания (#141)
  приходят внутри этого фрагмента, обходного канала к исходным данным нет.
- Бюджеты #125 пробрасываются как есть:
  `build(contexts, embedder, max_chars=..., max_tokens=..., count_tokens=...)`.
  `max_tokens` и `count_tokens` передаются только вместе.
- Выбор секций фрагмента (#146) в этот модуль не входит. Когда он появится
  в `to_llm_prompt_fragment`, его параметры пробрасываются тем же путём.

## Ошибки

Все ошибки наследуют `RagError(ValueError)`.

| Класс | Когда |
|---|---|
| `RagBuildError` | дубликат ключа в `scan_index`; ключ контекста не найден или повторяется; эмбеддер не callable или упал; вектор пустой, нечисловой (включая `bool`), содержит NaN/inf, имеет нулевую норму или другую размерность; `save()` до готовности индекса |
| `RagQueryError` | `query()` до `build()`/`load()` или после неудачного `load()`; `text` не строка; `top_k` не положительный `int`; эмбеддер упал; вектор запроса некорректен или другой размерности |
| `RagLoadError` | любая проблема артефактов (см. ниже) |

Сообщения стабильны и не содержат путей, текста исходных исключений и
ответов эмбеддера; исходное исключение не прикрепляется (`from None`).

Неудачный `build()` не меняет прежнее состояние индекса. Неудачный `load()`
переводит индекс в неготовое состояние: следующий `query()` бросит
`RagQueryError`, а не вернёт устаревший или пустой результат.

## Persistence

`save(index_dir)` пишет два файла:

- `rag_index.npz` — ZIP_STORED-архив с одним entry `vectors.npy`:
  NPY v1.0, dtype `<f8`, C-order, shape `(N, dim)`, фиксированная дата
  entry. Файл читается `numpy.load`, но numpy пакету не нужен.
- `rag_meta.json`:

```json
{
  "schema_version": 2,
  "count": 2,
  "dimension": 3,
  "vectors_sha256": "<sha256 байтов rag_index.npz>",
  "keys": [
    ["Catalog", "Справочник1", "CatalogForm", "ФормаСписка"],
    ["Catalog", "Справочник1", "CatalogForm", "ФормаЭлемента"]
  ]
}
```

Одинаковый индекс даёт одинаковые байты обоих файлов.

### Атомарность

Оба файла сначала пишутся во временные файлы в том же каталоге
(`fsync`), затем переносятся `os.replace()`: сначала архив, потом meta.
Сбой до переносов оставляет прежнюю пару нетронутой. Сбой между переносами
оставляет новый архив со старой meta; `vectors_sha256` не совпадает, и
`load()` отвергает такую пару. Временные файлы удаляются при любом исходе.
Ошибки файловой системы при `save()` пробрасываются как `OSError`.

### Что проверяет load()

`RagLoadError` бросается, если:

- нет или не читается `rag_meta.json` или `rag_index.npz`;
- meta — не JSON, не UTF-8 или не объект;
- `schema_version` не равен 2 (включая артефакты schema 1 из #78);
- `count`/`dimension` не неотрицательные `int` или противоречат друг другу;
- `vectors_sha256` отсутствует, некорректен или не совпадает с архивом;
- `keys` не список 4-строковых ключей, `len(keys) != count`, ключи
  повторяются или отсутствуют в `scan_index`;
- архив битый, содержит лишние entry или превышает лимит размера;
- NPY: не v1.0, не `<f8`, fortran-order, не 2D, размер тела не совпадает;
- число строк или размерность не совпадают с meta;
- сохранённый вектор содержит NaN/inf или имеет нулевую норму.

### Что можно и нельзя хранить в meta

Можно: `schema_version`, `count`, `dimension`, `vectors_sha256`, `keys`.

Нельзя: пути (абсолютные и относительные), BSL, текст фрагмента или
промпта, имена хостов, баз и пользователей, параметры провайдера, ключи API,
идентификаторы моделей. Новое поле meta — это изменение схемы с новым
`schema_version`.

## Rebuild policy

- Индекс — производный артефакт. Его пересобирают целиком после любого
  изменения `forms_scan_index.json`, смены эмбеддера или модели, смены
  бюджетов `max_chars`/`max_tokens`.
- Векторы разных эмбеддеров несравнимы. При смене эмбеддера старый индекс
  не догружают, а пересобирают.
- Инкрементального обновления нет: `build()` заменяет индекс полностью.
- `rag_index.npz` и `rag_meta.json` в репозиторий не коммитят. Векторы
  выводятся из содержимого конфигурации и могут частично его раскрыть;
  держите их рядом с локальной выгрузкой и исключайте из VCS.

## Синтетический пример без сети

```python
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from v8unpack_agent.form_context import FormContext
from v8unpack_agent.form_rag import FormRagIndex
from v8unpack_agent.form_summary import FormSummary
from v8unpack_agent.scan_forms import FormEntry, FormScanIndex


def toy_embedder(text: str) -> list[float]:
    """Детерминированный локальный эмбеддер: хэш слов по 8 корзинам."""
    vector = [0.0] * 8
    for word in text.lower().split():
        bucket = hashlib.sha256(word.encode("utf-8")).digest()[0] % 8
        vector[bucket] += 1.0
    vector[0] += 1e-6  # защита от нулевой нормы для пустого текста
    return vector


def make(root: Path, form_name: str) -> tuple[FormEntry, FormContext]:
    form_dir = root / "Catalog" / "Справочник1" / "CatalogForm" / form_name
    entry = FormEntry(
        object_type="Catalog",
        object_name="Справочник1",
        container_name="CatalogForm",
        form_name=form_name,
        form_path=form_dir,
        bsl_path=form_dir / "CatalogForm.obj.bsl",
        json_path=form_dir / "CatalogForm.json",
    )
    context = FormContext(
        form_name=form_name,
        container_name="CatalogForm",
        object_type="Catalog",
        object_name="Справочник1",
        bsl_text="Процедура ПриОткрытии(Отказ)\nКонецПроцедуры\n",
        summary=FormSummary(),
        metadata={},
    )
    return entry, context


with TemporaryDirectory() as tmp:
    root = Path(tmp)
    pairs = [make(root, "ФормаСписка"), make(root, "ФормаЭлемента")]
    scan_index = FormScanIndex(
        forms=[e for e, _ in pairs], total=len(pairs), scanned_at=""
    )

    index = FormRagIndex(scan_index)
    index.build([c for _, c in pairs], toy_embedder)
    for result in index.query("форма списка", top_k=2):
        print(result.matched[0].form_name, result.confidence)

    index.save(root / "rag")
    restored = FormRagIndex(scan_index)
    restored.load(root / "rag", toy_embedder)
    assert [r.matched for r in restored.query("форма списка")] == [
        r.matched for r in index.query("форма списка")
    ]
```

`toy_embedder` нужен только для демонстрации: смысловой близости он не
моделирует. Реальный эмбеддер подключает вызывающий код.
