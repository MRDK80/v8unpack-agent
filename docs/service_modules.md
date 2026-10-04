# Модули HTTP- и Web-сервисов

Модуль `v8unpack_agent.service_modules` обнаруживает и читает BSL-модули
HTTP-сервисов и Web-сервисов конфигурации 1С и возвращает общий контракт
`ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md) (#203) с
`module_kind="service"`. Реализация добавлена в issue #337 эпика #201
вместе со сканером констант
([`docs/value_manager_modules.md`](value_manager_modules.md)).

## Доказанные соответствия

Обе строки взяты из исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
с `owner_kind=metadata_object`, `module_kind=service` и доказательством
`designer_content_match_A`: текст файлов v8unpack совпал с `Ext/Module.bsl`
соответствующего сервиса выгрузки Конфигуратором в файлы.

Файл лежит непосредственно в каталоге сервиса:
`<metadata_type>/<owner_name>/<файл>`.

| `metadata_type` | Файл модуля сервиса | Модуль Конфигуратора | Наблюдения #202 |
|---|---|---|---|
| `HTTPService` | `HTTPService.obj.bsl` | `HTTPServices/{Name}/Ext/Module.bsl` | A: 1 файл; B, C: нет |
| `WebService` | `WebService.obj.bsl` | `WebServices/{Name}/Ext/Module.bsl` | A: 3, B: 1; C: нет |

HTTP-сервис доказан совпадением текста с Конфигуратором, но наблюдался
только в одной выгрузке и одним файлом; повторяемость на других выгрузках
проверяется по мере появления данных (#209).

### Различие HTTP и Web в данных

У обоих типов `module_kind="service"`; различие хранится в поле записи
`metadata_type` (`HTTPService` или `WebService`), которое входит в
`module_id` и в JSON `module_index/1`. Новые значения закрытых наборов,
поля и версия схемы не вводились. Поэтому `index.filter(metadata_type=
"HTTPService")` и `index.filter(metadata_type="WebService")` разделяют
сервисы, а одноимённые HTTP- и Web-сервисы получают разные `module_id`:

| Сервис | `module_id` |
|---|---|
| `HTTPService/Alpha/HTTPService.obj.bsl` | `metadata_object:HTTPService:Alpha:service` |
| `WebService/Alpha/WebService.obj.bsl` | `metadata_object:WebService:Alpha:service` |

### Не поддержано

| Случай | Причина |
|---|---|
| вложенные каталоги сервисов (`URLTemplate/...`, `Ext/`), иные файлы в каталоге сервиса | другие артефакты или не normalized-layout; записей не дают |
| `WSReference` и иные типы, не встреченные в #202 | не доказаны |
| расширения, внешние обработки и отчёты | в #202 not_detected |
| raw-layout и выгрузка Конфигуратором (`HTTPServices/{Name}/Ext/Module.bsl`, `WebServices/...`) | исследован только normalized-layout v8unpack |

Недоказанные случаи не классифицируются догадкой и не дают записей, в том
числе `missing`. Сервисы не исполняются, сетевые вызовы не выполняются.

## API

```python
from pathlib import Path

from v8unpack_agent.service_modules import (
    SERVICE_MODULE_FILES,
    scan_service_modules,
)

index = scan_service_modules(Path("cf_export"))
print(index.filter(metadata_type="HTTPService").to_json())
```

- `SERVICE_MODULE_FILES` — неизменяемое отображение
  `metadata_type → имя файла` из таблицы выше.
- `scan_service_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

Поля записей: `module_kind="service"`, `owner_kind="metadata_object"`,
`metadata_type` — каталог верхнего уровня, `owner_name` — имя каталога
сервиса, `module_id` — `metadata_object:<metadata_type>:<owner_name>:service`.
Идентификатор не зависит от пути и не пересекается с записями сканеров
#204–#207 и констант, поэтому индексы объединяются через
`ModuleIndex.from_entries` без конфликтов.

| Состояние | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| каталог типа не читается (`iterdir()`) | `OSError` пробрасывается |
| нет каталогов `HTTPService/` и `WebService/` | пустой `ModuleIndex` |
| два каталога сервиса одного типа, различающиеся только регистром букв | `ValueError` (дубликат `module_id` по контракту #203) |

Политика ошибок совпадает со сканерами #204–#207.

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл на месте модуля |
| `missing` | файла нет по доказанному пути у существующего сервиса |

Запись `missing` создаётся только при доказанном основании ожидать файл:
существует обычный каталог `HTTPService/<owner_name>` или
`WebService/<owner_name>`. Без каталога сервиса запись не создаётся. Это
основание выбрано по layout #202: у сервиса ровно один модуль, и файл
лежит прямо в его каталоге.

`missing` означает «файла нет», а не «модуля нет» и не ошибку: #202
показал, что v8unpack не записывает часть пустых модулей. Уровень отчёта
для таких записей — вопрос #208.

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing`
и для `read_error` без чтения байтов оба поля — `None`.

## Владельцы

Владелец — непосредственный подкаталог каталога типа. Пропускаются без
записей:

- symlink и иные не-обычные каталоги типов и сервисов (они не
  разыменовываются), а также каталоги, разрешённый путь которых не лежит
  непосредственно в родительском каталоге;
- файлы в каталоге типа;
- каталоги, имя которых не является идентификатором 1С или даёт путь,
  недопустимый по `validate_relative_module_path()` (например,
  зарезервированные имена устройств Windows).

Каталоги типов сравниваются по точному имени (`HTTPService`,
`WebService`); варианты в другом регистре и во множественном числе не
классифицируются.

## Безопасность

- Сканер только читает: `lstat()`, `iterdir()`, `resolve()` и
  `read_bytes()`. Он не создаёт и не изменяет файлы и не вызывает
  `scan_forms()`, который пишет JSON в каталоги форм.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. `owner_name` содержит реальные имена
  сервисов: обезличенные отчёты должны агрегировать данные на своём уровне.

## Порядок и повторяемость

Типы и сервисы обходятся в отсортированном порядке; итоговый порядок задаёт
`ModuleIndex` (OS-нейтральная сортировка по относительному пути), JSON
`module_index/1` детерминирован. Порядок `iterdir()` на результат не влияет.

## Вне scope

Исполнение сервисов, разбор URL-шаблонов и операций, модули объектов,
менеджеров, наборов записей и команд (#205–#207), runner, CLI, post-run
report и LLM-контекст (#208), итоговая матрица покрытия (#209), RAG,
разбор BSL и изменения upstream v8unpack.
