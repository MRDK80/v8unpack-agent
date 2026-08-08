# Компактный LLM-контекст формы: form_context

Issue #77. Модуль `v8unpack_agent/form_context.py`.

## Концепция

`FormEntry` из `scan_forms` — **карточка указателей**: она знает, где лежат
части формы, но не содержит их содержимого. `FormContext` — **материализованное
содержимое**: прочитанный BSL-текст, построенный `FormSummary` и компактные
метаданные, пригодные для вставки в промпт.

```
scan_forms(root)                 -> FormScanIndex / FormEntry (указатели)
  \_ build_form_context(entry, root) -> FormContext (содержимое)
       \_ build_form_summary(form_dir) -> FormSummary (единственный парсер)
            \_ parse_elem_json(form_dir) -> ElemIndexResult
       \_ to_llm_prompt_fragment(context, max_chars) -> текст для промпта
```

Второго пути разбора не вводится. Структуру формы даёт только
`build_form_summary` поверх `parse_elem_json`; `form_context` ничего не парсит
самостоятельно и не создаёт привязок `data_path`.

## Публичный API

| Символ | Назначение |
|--------|------------|
| `FormContext` | `frozen`-датакласс с содержимым одной формы. |
| `build_form_context(form_entry, unpacked_root)` | Материализует содержимое по карточке `FormEntry`. |
| `to_llm_prompt_fragment(context, max_chars=8000)` | Компактный детерминированный текст для промпта. |

```python
from pathlib import Path

from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.scan_forms import scan_forms

root = Path("path/to/cf_export")
index = scan_forms(root)

context = build_form_context(index.forms[0], root)
fragment = to_llm_prompt_fragment(context, max_chars=4000)
```

## FormContext

```python
@dataclass(frozen=True)
class FormContext:
    form_name: str
    container_name: str
    object_type: str
    object_name: str
    bsl_text: str | None
    summary: FormSummary
    metadata: dict
```

`frozen=True` при изменяемом `dict` — та же комбинация, что уже принята в
`FormSummary` со списками: подмена полей запрещена, глубокой неизменяемости нет.

### Зачем нужен unpacked_root

Семантика путей `FormEntry` смешанная (issue #57):

| Поле | Вид пути |
|------|----------|
| `form_path` | абсолютный |
| `bsl_path` | абсолютный (у elem-only форм — заглушка на несуществующий файл) |
| `json_path` | абсолютный |
| `elem_json_path` | relative-to-root, `Optional` |

`unpacked_root` решает три задачи: резолвит относительный `elem_json_path`,
служит базой для обезличенных относительных путей в `metadata` и вырезается из
текстов предупреждений парсера.

### Состав metadata

Отбор, а не копия `FormEntry`. Ровно шесть ключей:

| Ключ | Значение |
|------|----------|
| `form_path` | относительный posix-путь каталога формы |
| `elem_json_path` | относительный posix-путь `*.elem.json` либо `None` |
| `bsl_sha256` | хэш модуля формы из реестра либо `None` |
| `elem_sha256` | хэш структуры формы из реестра либо `None` |
| `has_bsl` | прочитан ли модуль формы фактически |
| `warnings` | предупреждения `FormEntry`, обезличенные |

`json_path`, `bsl_path`, `bsl_mtime` и `form_elem_path` в `metadata` не
попадают: дублировать всю карточку — вне scope #77.

## Поведение при отсутствующих артефактах

| Ситуация | Результат |
|----------|-----------|
| BSL есть | `bsl_text` — содержимое, прочитанное явно как UTF-8 |
| BSL отсутствует (elem-only форма) | `bsl_text is None`, `metadata["has_bsl"] is False` |
| BSL пустой файл | `bsl_text == ""` — отличается от `None` |
| `*.elem.json` отсутствует | пустые бакеты `FormSummary` и `warnings` парсера |
| `elem_json_path is None` (старый индекс) | каталог формы берётся из `form_path` |
| каталога формы нет вовсе | пустая выжимка с предупреждением, без вызова парсера |

Отсутствующий файл никогда не превращается в выдуманные данные. Ошибки чтения
не подавляются: `best-effort` применяется только там, где он уже следует
контракту `FormSummary`.

## Формат фрагмента и truncation contract

```
# FORM <object_type>/<object_name>/<container_name>/<form_name>
## SUMMARY
<to_normalized_json(summary)>
## BSL
<bsl_text либо «(модуль формы отсутствует)»>
```

Гарантии:

- порядок фиксирован: summary всегда раньше BSL, потому что смысловая выжимка
  важнее кода;
- обрезка выполняется **последним** шагом по символам, поэтому
  `len(result) <= max_chars` выполняется при любых входных данных, включая
  лимит меньше длины заголовков;
- `max_chars <= 0` даёт пустую строку — запрошен нулевой бюджет, выдан нулевой
  бюджет, исключения нет;
- результат детерминирован: два вызова на одинаковых данных дают идентичный
  текст.

## Обезличенность

`parse_elem_json` формирует часть предупреждений с абсолютным путём каталога
формы. Для локальной диагностики это полезно, но `FormContext` предназначен для
промпта и отчётов, поэтому база `unpacked_root` из текстов вырезается —
остаётся относительный путь, содержательная часть предупреждения сохраняется.
Парсер при этом не менялся: чистка живёт на границе контекста.

## Синтетический пример

```python
import json
from pathlib import Path

from v8unpack_agent.form_context import build_form_context, to_llm_prompt_fragment
from v8unpack_agent.scan_forms import FormEntry

form_dir = Path("tmp/Catalog/Объект/CatalogForm/ФормаЭлемента")
form_dir.mkdir(parents=True, exist_ok=True)
(form_dir / "CatalogForm.obj.bsl").write_text("// код формы\n", encoding="utf-8")
(form_dir / "CatalogForm.elem.json").write_text(
    json.dumps(
        {
            "tree": [{"name": "Таблица", "type": "Table", "ПутьКДанным": "Объект.Товары"}],
            "data": {"-pages-": ["Страница1"], "Страница1/Таблица": {"id": 1}},
            "props": [{"name": "Реквизит", "type": "String"}],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

entry = FormEntry(
    object_type="Catalog",
    object_name="Объект",
    container_name="CatalogForm",
    form_name="ФормаЭлемента",
    form_path=form_dir.resolve(),
    bsl_path=(form_dir / "CatalogForm.obj.bsl").resolve(),
    json_path=(form_dir / "CatalogForm.json").resolve(),
    elem_json_path=Path("Catalog/Объект/CatalogForm/ФормаЭлемента/CatalogForm.elem.json"),
)

context = build_form_context(entry, Path("tmp"))
print(context.metadata["form_path"])          # относительный путь
print(len(to_llm_prompt_fragment(context, max_chars=500)) <= 500)  # True
```

Запускаемый вариант — `examples/form_context.py`.

## Ограничения

- `FormContext` не индексирует и не ищет: RAG (`form_rag`, #78) и
  диспетчеризация (`form_dispatcher`, #79) в scope не входят.
- Привязки `data_path` не создаются и не достраиваются; форма без подтверждённых
  привязок даёт выжимку без `relations` — это результат, а не дефект.
- CLI-команды у модуля нет.
- Обрезка выполняется по символам, а не по токенам: `max_chars` — бюджет
  символов, соответствие числу токенов конкретной модели не гарантируется.
- Обрезка может разорвать JSON выжимки на границе лимита: фрагмент
  предназначен для чтения моделью, а не для машинного разбора.
- Экспорт нового API из корня пакета (`v8unpack_agent/__init__.py`) в этой
  задаче не выполнялся — импорт идёт из `v8unpack_agent.form_context`.
