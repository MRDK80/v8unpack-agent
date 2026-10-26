# Поисковый корпус: адаптеры форм и модулей (#323)

Модуль `v8unpack_agent.search_corpus` превращает результаты существующих
сканеров в документы и связи контракта `search_contract/1`
([`search_contract.md`](search_contract.md), #322). Новых обходов выгрузки
нет: модули берутся из `ModuleIndex` (#203–#207, #337, #351) и
`CommonModuleIndex` (#151), формы — из `FormContext` (#77). Эмбеддинги,
индекс, хранение, фрагментация процедур (#324), CLI и генерация ответа в
модуль не входят.

## Импорт

```python
from v8unpack_agent.search_corpus import (
    FormReadFailure,
    SearchCorpus,
    build_form_inputs,
    build_search_corpus,
    read_module_text,
)
```

Корневой `v8unpack_agent.__init__` не расширяется; API форм, модулей и
контракта #322 не меняется.

## Вход и результат

```python
forms = build_form_inputs(form_scan_index.forms, export_root)
corpus = build_search_corpus(
    export_root,
    module_index=module_index,          # scan_*_modules
    common_modules=common_module_index,  # scan_common_modules
    forms=forms,                         # FormContext | FormReadFailure
)
corpus.documents    # SearchDocumentSet
corpus.owner_links  # tuple[OwnerLink, ...]
corpus.report       # CorpusReport
```

`build_form_inputs()` вызывает существующий `build_form_context()`;
`OSError` и `UnicodeDecodeError` при построении контекста дают
`FormReadFailure`, а не прерывание сборки. Можно передать и уже готовые
`FormContext`.

`SearchCorpus.to_json()` и `CorpusReport.to_json()` детерминированы
(схема `search_corpus/1`) и не содержат текста документов; текст
сериализуется только через `corpus.documents.to_json(include_text=True)`.
Порядок результата не зависит от порядка входа.

## Чтение текста

`read_module_text(export_root, entry)` читает файл только по
`ModuleEntry.relative_path`, который построил сканер, без повторного
угадывания layout:

- статус сканера, отличный от `ok`, не перечитывается и становится причиной;
- symlink, не обычный файл и выход за `export_root` — `unsafe_path`;
- `OSError` и невалидный UTF-8 — `read_error`, исчезнувший файл — `missing`;
- `sha256` записи не совпал с байтами — `content_changed`;
- текст без ведущего BOM, CRLF и CR заменены на LF, как в
  `to_llm_module_fragment` (#345) и `TextSpan` (#322).

Общие модули читаются тем же способом по `CommonModuleEntry.bsl_path` и
адаптируются через `module_entry_from_common_module()`; статус
`whitespace_only` различается. Текст формы — `FormContext.bsl_text`.

## Документы и исключения

Документ создаётся только из прочитанного непустого текста: один целый
документ на артефакт (`document_id == artifact_id`). Вымышленный текст для
отсутствующих модулей не создаётся.

| Причина | Когда |
|---|---|
| `empty`, `whitespace_only` | текст пуст или из пробелов |
| `missing` | файла нет; у формы — `bsl_text is None` (elem-only) |
| `read_error` | ошибка чтения или декодирования, `FormReadFailure` |
| `unsafe_path` | symlink, не файл или путь вне корня |
| `content_changed` | байты не совпали с `sha256` сканера |
| `form_module_via_form_context` | запись `module_kind="form"` в `ModuleIndex` |
| `duplicate_artifact` | тот же `artifact_id` уже взят из другого входа |
| `invalid_identity` | из ключа формы нельзя построить `ArtifactRef` |

`CorpusReport` перечисляет каждый входной артефакт: `artifact_id`, вид,
источник (`module_index`, `common_modules`, `forms`, `owner_cards`),
относительный `source_path`, `included`, `reason`, владелец и основание
связи. `report.read_errors` — строки `read_error` и `unsafe_path`,
`report.counts()` — число строк по источнику и исходу.

## Дедупликация

- Модуль формы учитывается только как артефакт формы
  `form:<object_type>/<object_name>/<container_name>/<form_name>`; запись
  `module_kind="form"` в `ModuleIndex` исключается с причиной
  `form_module_via_form_context`. Так же формы представлены в eval-наборах
  #321.
- Общий модуль, пришедший и из `ModuleIndex`, и из `CommonModuleIndex`,
  даёт один документ; приоритет входов: `module_index`, `common_modules`,
  `forms`, `owner_cards`. Повтор помечается `duplicate_artifact`.

## Владельцы и связи

| Артефакт | Владелец | `basis` |
|---|---|---|
| модуль | владелец `ModuleEntry` | `module_entry` |
| форма 4-уровневого layout | `metadata_object` с `metadata_type=object_type`, `owner_name=object_name` | `form_key` |
| общая форма (`CommonForm/<Форма>`) | `common_form` `CommonForm` `<Форма>` | `unconfirmed` |
| форма внешней обработки или отчёта | владелец модуля объекта (#351) из того же каталога артефакта | `unconfirmed` |

4-уровневый layout считается доказанным, только если относительный
`metadata["form_path"]` из `FormContext` совпадает с четвёркой
`(object_type, object_name, container_name, form_name)`, `object_type` не
`CommonForm` и не внешний тип, а `object_name` — идентификатор 1С. Тогда
`ArtifactRef.owner` формы задан.

Для общих и внешних форм контракт #322 не даёт основания: `form_key`
требует `owner_name == object_name`, а у общей формы `object_name` пуст, у
внешней — имя каталога распаковки, тогда как имя владельца берётся из
`ExternalDataProcessor.json`. Такие связи остаются `unconfirmed`, а
`ArtifactRef.owner` формы — `None`. Подтверждение потребует отдельного
согласованного расширения `LinkBasis`.

## Карточки владельцев

Карточка — документ `owner:<owner_kind>:<metadata_type>:<owner_name>` для
каждого владельца хотя бы с одной подтверждённой связью. Текст содержит
только вид, тип и имя владельца и подтверждённые связи:

```text
OWNER
owner_kind: metadata_object
metadata_type: Catalog
owner: Alpha
links:
form form:Catalog/Alpha/CatalogForm/Main basis=form_key
module metadata_object:Catalog:Alpha:object basis=module_entry
```

Синонима в `ModuleEntry` нет, поэтому строки синонима нет. Связи
`unconfirmed` в карточки не входят и доступны в `owner_links` с
`confirmed=False`. Связь команды объекта с объектом и другие
неподтверждённые иерархии не выводятся. Абсолютных путей в карточках и
отчёте нет: `source_path` проверяется `validate_relative_source_path`.

## common_command и common_form в eval-наборе

Eval-наборы #321 не изменялись. `common_command` подключён через
`scan_command_modules`, `common_form` — через `FormContext`; оба вида
покрыты синтетическими тестами `tests/test_search_corpus_issue323.py`.
Это структурное покрытие, а не доказательство качества поиска: оценка
качества для этих видов относится к #357 и #329.

## Ограничения для #324

- Один документ на артефакт; фрагменты по процедурам и окна `#L<a>-<b>`
  строит #324 поверх текста корпуса в координатах `TextSpan`.
- Текст формы — только модуль формы; структура формы, реквизиты и
  заголовок документа в текст не входят.
- Формы без модуля (`missing`) документов не дают, но видны в отчёте.

Правила фрагментации #324, исходные срезы и отдельный `search_text`,
окна больших процедур и диагностика описаны в
[`search_fragments.md`](search_fragments.md). Контракт `search_contract/1`
и исходный корпус не изменяются.
