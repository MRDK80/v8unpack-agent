# Компактный LLM-контекст формы: form_context

Issue #77, #NEW. Модуль `v8unpack_agent/form_context.py`.

## Концепция

`FormEntry` из `scan_forms` — **карточка указателей**: она знает, где лежат
части формы, но не содержит их содержимого. `FormContext` — **материализованное
содержимое**: прочитанный BSL-текст, построенный `FormSummary`, компактные
метаданные, а также (issue #NEW) реквизиты и табличные части объекта
метаданных за формой.

```
scan_forms(root)                 -> FormScanIndex / FormEntry (указатели)
  \_ build_form_context(entry, root) -> FormContext (содержимое)
       \_ build_form_summary(form_dir) -> FormSummary (единственный парсер)
            \_ parse_elem_json(form_dir) -> ElemIndexResult
       \_ object_json_path(entry) -> Path | None            (catalog_resolver, issue #NEW)
            \_ decode_object_attributes(object_json, type_resolver) -> DecodeResult   (object_decoder, issue #NEW)
       \_ resolve_data_path(data_path, object_json) -> ResolvedBinding (catalog_resolver, issue #NEW,
                                                                        только для relations с kind=="data")
       \_ to_llm_prompt_fragment(context, max_chars) -> текст для промпта
```

Второго пути разбора формы не вводится. Структуру самой формы даёт только
`build_form_summary` поверх `parse_elem_json`; `form_context` сам ничего не парсит
и не создаёт новых привязок `data_path`. Для реквизитов самого объекта
метаданных (issue #NEW) аналогично используется только уже существующий в репозитории
`object_decoder` и `catalog_resolver` — без второго пути разбора raw-header.

## Публичный API

| Символ | Назначение |
|--------|------------|
| `FormContext` | `frozen`-датакласс с содержимым одной формы. |
| `build_form_context(form_entry, unpacked_root, *, type_resolver=None)` | Материализует содержимое по карточке `FormEntry`; `type_resolver` — опциональный резолвер ссылочных типов `uuid -> имя типа`. |
| `to_llm_prompt_fragment(context, max_chars=-1)` | Детерминированный текст для промпта; по умолчанию без обрезки. |

Символы доступны двумя равнодопустимыми путями: из корняй пакета
(`from v8unpack_agent import FormContext, build_form_context, to_llm_prompt_fragment`)
и напрямую из подмодуля (`from v8unpack_agent.form_context import ...`).
Корневой экспорт ленивый: `import v8unpack_agent` не загружает `form_context`,
модуль импортируется при первом обращении к символу (issue #124).

> С issue #128 гарантия расширена: `import v8unpack_agent` не загружает ни
> `form_context`, ни `elem_parser`, ни `pipeline`.

```python
from pathlib import Path

from v8unpack_agent import build_form_context, to_llm_prompt_fragment
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
    object_attributes: dict | None = None       # issue #NEW
    resolved_relations: list[dict] = field(default_factory=list)  # issue #NEW
```

`frozen=True` при изменяемых `dict`/`list` — та же комбинация, что уже принята в
`FormSummary` со списками: подмена самих полей заборошена, глубокой неизменяемости нет.

### Zachem nuzhen unpacked_root

Семантика путей `FormEntry` смешанная (issue #57):

| Поле | Вид пути |
|------|----------|
| `form_path` | абсолютный, указывает на директорию формы (не на файл) |
| `bsl_path` | абсолютный (у elem-only форм — заглушка на несуществующий файл) |
| `json_path` | абсолютный |
| `elem_json_path` | relative-to-root, `Optional` |

`unpacked_root` решает три задачи: резолвит относительный `elem_json_path`,
служит базой для обезличенных относительных путей в `metadata` и вырезается из
текстов предупреждения парсера.

**Важно (issue #NEW):** `object_json_path()` из `catalog_resolver` ожидает, что `form_entry.form_path`
указывает именно на директорию формы (тот же путь, что использует `_form_dir` в fallback-ветке):
он поднимается на два уровня вверх от `form_path`, чтобы найти каталог объекта
(`.../<тип>/<объект>/Forms/<игра>` -> `.../<тип>/<объект>`). Передача в `form_path` пути до файла
внутри директории сдвигает расчёт уровней и даёт `object_attributes = None`.

### Состав metadata

Отбор, а не копия `FormEntry`. Ровно шесть ключей — набор не изменился issue #NEW,
чтобы сохранить строгий контракт, зафиксированный `test_metadata_keys_are_exactly_expected`.
Наличие реквизитов объекта проверяется напрямую через `context.object_attributes is not None`,
отдельного булева в `metadata` для этого нет.

| Ключ | Значение |
|------|----------|
| `form_path` | относительный posix-путь каталога формы |
| `elem_json_path` | относительный posix-путь `*.elem.json` либо `None` |
| `bsl_sha256` | хэш модуля формы из реестра либо `None` |
| `elem_sha256` | хэш структуры формы из реестра либо `None` |
| `has_bsl` | прочитан ли модуль формы фактически |
| `warnings` | предупреждения `FormEntry` и `object_decoder`, обезличенные |

`json_path`, `bsl_path`, `bsl_mtime` и `form_elem_path` в `metadata` не
попадают: дублировать всю карточку — вне scope #77.

## object_attributes и resolved_relations (issue #NEW)

### object_attributes

Реквизиты и табличные части объекта метаданных (а не самой формы) за ней. Получается
двухшагово: `catalog_resolver.object_json_path(form_entry)` находит JSON-файл объекта,
`object_decoder.decode_object_attributes(object_json)` декодирует его raw `header`.

| Ситуация | Результат |
|----------|----------|
| файл объекта найден и декодирован | `{"Properties": [...], "TabularSections": [...]}` |
| файл объекта не найден | `object_attributes is None`, запись в `metadata["warnings"]` |
| декодирование не удалось (`DecodeResult.ok is False`) | `object_attributes is None`, предупреждения `object_decoder` в `metadata["warnings"]` |

`None` отличается от пустой структуры `{"Properties": [], "TabularSections": []}` и не подменяется на неё:
отсутствие файла — это отдельный случай от найденного, но без реквизитов объекта.

`object_json_path()` должен указывать на object JSON в raw-header layout.
Если файл не поддерживается декодером — например, содержит нормализованные
`Properties` / `TabularSections` без ключа `header` — `object_attributes`
остаётся `None`, причина из `DecodeResult` сохраняется в `metadata["warnings"]`,
а контекст формы всё равно строится. Пустая структура вместо неуспешного
декодирования не подставляется, а нормализованные данные не принимаются
passthrough — контракт входного layout описан в
[`object_decoder`](object_decoder.md) (#160).

`type_resolver` передаётся, если его передал вызывающий (issue #147). Без него
поведение прежнее: ссылочные типы остаются в виде `Ref#<uuid>`, догадка о типе
метаданных не делается. Источником имён служит готовый индекс #88 —
`FormScanIndex.resolve_reference_type`.

### resolved_relations

Обогащение `summary.relations` типом и синонимом реквизита через
`catalog_resolver.resolve_data_path()`. Обрабатываются только связи с `kind == "data"`:
это единственные связи с `data_path`, для которых резолюция по файлу объекта
имеет смысл; связи `kind == "event"` не трогаются.

Каждая запись — словарь `{data_path, object_type, attribute_name, value_type, synonym, resolved}`.
При `object_json is None` или неудачной резолюции запись всё равно возвращается с
`resolved=False`, а не отбрасывается — потребитель видит весь список data-связей формы,
даже если часть из них не разрешилась. Новые привязки `data_path` при этом не создаются —
только обогащаются те, которые уже выдал `elem_parser`.

## Ссылочные типы реквизитов (issue #147)

`object_attributes` заполняется через `object_decoder.decode_object_attributes()`.
По умолчанию ссылочный тип остаётся в виде `Ref#<uuid>`: модуль не строит
собственного индекса метаданных и не угадывает тип. Готовый индекс живёт в
`FormScanIndex` (#88), поэтому резолвер передаётся снаружи:

```python
from v8unpack_agent import build_form_context
from v8unpack_agent.scan_forms import scan_forms

index = scan_forms(root)
context = build_form_context(
    index.forms[0],
    root,
    type_resolver=index.resolve_reference_type,
)
```

| Условие | Поведение |
|---------|-----------|
| `type_resolver` не передан | `Ref#<uuid>` сохраняется, результат идентичен прежнему |
| резолвер вернул строку | `Type` заменяется читаемым именем (`CatalogRef.Контрагенты`) |
| резолвер вернул `None` | остаётся `Ref#<uuid>`, тип не угадывается |
| резолвер бросил исключение | `REF_RESOLVER_FAILED` в `metadata["warnings"]`, тип остаётся `Ref#<uuid>` |

Контракт:

- параметр keyword-only, вызовы `build_form_context(entry, root)` работают без
  изменений;
- резолвер получает UUID **без** префикса `Ref#`;
- преобразование применяется к `Properties` и к реквизитам `TabularSections`;
- входные структуры и сам индекс не мутируются;
- `resolved_relations` этот параметр не затрагивает: их строит
  `catalog_resolver.resolve_data_path()` своим вызовом декодера (#148).

## Поведение при отсутствующих артефактах

| Ситуация | Результат |
|----------|----------|
| BSL есть | `bsl_text` — содержимое, прочитанное явно как UTF-8 |
| BSL отсутствует (elem-only форма) | `bsl_text is None`, `metadata["has_bsl"] is False` |
| BSL пустой файл | `bsl_text == ""` — отличается от `None` |
| `*.elem.json` отсутствует | пустые бакеты `FormSummary` и `warnings` парсера |
| `elem_json_path is None` (старый индекс) | каталог формы берётся из `form_path` |
| каталога формы нет вовсе | пустая выжимка с предупреждением, без вызова парсера |
| `object_json_path()` не нашёл файл объекта (issue #NEW) | `object_attributes is None`, `resolved_relations` с `resolved=False`, предупреждение в `metadata["warnings"]` |
| `decode_object_attributes()` вернул `ok=False` (issue #NEW) | `object_attributes is None`, предупреждения `DecodeResult.warnings` в `metadata["warnings"]` |

Отсутствующий файл никогда не превращается в выдуманные данные. Ошибки чтения
не подавляются: `best-effort` применяется только там, где он уже следует
контракту `FormSummary`/`DecodeResult`.

## Формат фрагмента и truncation contract

```
# FORM <object_type>/<object_name>/<container_name>/<form_name>
## SUMMARY
<to_normalized_json(summary)>
## OBJECT_ATTRIBUTES
<JSON с Properties/TabularSections/ResolvedRelations либо «(реквизиты объекта не найдены)»>
## BSL
<bsl_text либо «(модуль формы отсутствует)»>
```

Гарантии:

- порядок фиксирован: SUMMARY всегда раньше OBJECT_ATTRIBUTES, а OBJECT_ATTRIBUTES раньше
  BSL, потому что смысловая выжимка важнее кода;
- значение по умолчанию `max_chars=-1` возвращает полный контекст без обрезки;
- обрезка выполняется **последним** шагом по символам, поэтому
  при `max_chars > 0` выполняется `len(result) <= max_chars`, включая лимит
  меньше длины заголовков; при типичных размерах резаться будет именно
  хвост `## BSL`, так как SUMMARY и OBJECT_ATTRIBUTES идут раньше в тексте;
- `max_chars == 0` и значения меньше `-1` дают пустую строку;
- результат детерминирован: два вызова на одинаковых данных дают идентичный
  текст.

## Обезличенность

Начиная с issue #123 `parse_elem_json` обезличивает предупреждения в источнике.
Поэтому `FormSummary.warnings` безопасны и при использовании без
`FormContext`: локальный корень, буква диска Windows и UNC-хост не публикуются,
а для диагностики сохраняется значимый хвост пути с типом, объектом,
контейнером и формой. Разделители `/` и `\` обрабатываются одинаково на любой
ОС. Текст исключения очищается отдельно, поскольку `OSError` может повторить
абсолютный путь внутри собственного сообщения.

`form_context._strip_root` при этом сохранён. Он по-прежнему очищает warnings
из `FormEntry` и итоговой выжимки относительно `unpacked_root`, а также (issue #NEW)
предупреждения `object_decoder`, то есть остаётся вторым эшелоном защиты на границе
  LLM-контекста. Цели защиты не изменились: безопасный источник не отменяет
защиту потребителя от старого индекса, стороннего warning или будущей регрессии.

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
print(context.object_attributes)               # None — в примере нет Catalog.json
print(len(to_llm_prompt_fragment(context, max_chars=500)) <= 500)  # True
```

Зависимый вариант — `examples/form_context.py`.

## Ограничения

- `FormContext` не индексирует и не ищет: RAG (`form_rag`, #78) и
  диспетчеризация (`form_dispatcher`, #79) в scope не входят.
- Новые привязки `data_path` не создаются и не достраиваются: `resolved_relations`
  (issue #NEW) только обогащает типом/синонимом те, привязки, которые уже выдал
  `elem_parser`; форма без подтверждённых привязок даёт выжимку без `relations` — это
  результат, а не дефект.
- `object_attributes` отражает только то, что `object_decoder` смог расшифровать в raw `header`;
  для production-layout это best-effort с несколькими fallback-слоями
  (`_decode_real_header`), полнота покрытия всегда безоговорочна.
- Ссылочные типы (`Ref#<uuid>`) разрешаются только при переданном
  `type_resolver` (issue #147) и только для UUID, известных резолверу;
  неизвестный UUID сохраняет безопасный `Ref#<uuid>`, тип не угадывается.
- CLI-команды у модуля нет.
- Обрезка выполняется по символам, а не по токенам: `max_chars` — бюджет
  символов; `-1` означает отсутствие бюджета и обрезки. Соответствие числу
  токенов конкретной модели не гарантируется.
- Обрезка может разорвать JSON выжимки/object_attributes на границе лимита:
  фрагмент предназначен для чтения моделью, а не для машинного разбора.

### Диагностика отсутствия объекта-владельца (#172)

| Условие | Предупреждение |
|---|---|
| `object_name == ""` (нет уровня владельца) | `object_context: объект-владелец отсутствует по layout` |
| `object_name` непустой, файл владельца не найден | `object_context: файл объекта метаданных не найден` |

Публичный формат `FormContext`, ключ `metadata["warnings"]` и возвращаемый
tuple приватного хелпера не изменились.
