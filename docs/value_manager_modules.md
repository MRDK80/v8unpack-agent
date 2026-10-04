# Модули менеджеров значений констант

Модуль `v8unpack_agent.value_manager_modules` обнаруживает и читает
BSL-модули менеджеров значений констант конфигурации 1С и возвращает общий
контракт `ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md)
(#203) с `module_kind="value_manager"`. Реализация добавлена в issue #337
эпика #201 вместе со сканером сервисов
([`docs/service_modules.md`](service_modules.md)).

## Доказанное соответствие

Единственная строка взята из исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
с `owner_kind=metadata_object`, `module_kind=value_manager` и доказательством
`designer_content_match_A`: текст файлов v8unpack совпал с модулем
`ValueManagerModule.bsl` выгрузки Конфигуратором в файлы (17 из 17
непустых модулей выгрузки A).

Файл лежит непосредственно в каталоге константы:
`<metadata_type>/<owner_name>/<файл>`.

| `metadata_type` | Файл модуля менеджера значения | Модуль Конфигуратора | Наблюдения #202 |
|---|---|---|---|
| `Constant` | `Constant.obj.bsl` | `Constants/{Name}/Ext/ValueManagerModule.bsl` | A: 39 файлов (22 пустых), C: 9 (4 пустых), B: нет |

Суффикс `obj` сам по себе не определяет вид модуля: у справочников и
документов это модуль объекта, у перечислений — модуль менеджера, у
регистров — модуль набора записей. Здесь `value_manager` задаёт пара
«тип `Constant` + имя файла». Модуль константы не классифицируется как
`object`, `manager` или `form`; сканеры #205 и #206 каталог `Constant/` не
обходят, `METADATA_OBJECT_MODULE_LAYOUTS` и `RECORD_SET_MODULE_FILES` ради
`value_manager` не расширяются.

### Не поддержано

| Случай | Причина |
|---|---|
| формы констант, `Ext/`, иные файлы в каталоге константы | другие виды модулей или не normalized-layout; записей не дают |
| расширения, внешние обработки и отчёты | в #202 not_detected |
| raw-layout и выгрузка Конфигуратором (`Constants/{Name}/Ext/ValueManagerModule.bsl`) | исследован только normalized-layout v8unpack |

Недоказанные случаи не классифицируются догадкой и не дают записей, в том
числе `missing`.

## API

```python
from pathlib import Path

from v8unpack_agent.value_manager_modules import (
    VALUE_MANAGER_MODULE_FILES,
    scan_value_manager_modules,
)

index = scan_value_manager_modules(Path("cf_export"))
print(index.filter(read_status="ok").to_json())
```

- `VALUE_MANAGER_MODULE_FILES` — неизменяемое отображение
  `metadata_type → имя файла` из таблицы выше.
- `scan_value_manager_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

Поля записей: `module_kind="value_manager"`, `owner_kind="metadata_object"`,
`metadata_type="Constant"`, `owner_name` — имя каталога константы,
`module_id` — `metadata_object:Constant:<owner_name>:value_manager`.
Идентификатор не зависит от пути и не пересекается с записями сканеров
#204–#207 и сервисов, поэтому индексы объединяются через
`ModuleIndex.from_entries` без конфликтов.

| Состояние | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| каталог `Constant/` не читается (`iterdir()`) | `OSError` пробрасывается |
| каталога `Constant/` нет | пустой `ModuleIndex` |
| два каталога констант, различающиеся только регистром букв | `ValueError` (дубликат `module_id` по контракту #203) |

Политика ошибок совпадает со сканерами #204–#207.

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл на месте модуля |
| `missing` | файла нет по доказанному пути у существующей константы |

Запись `missing` создаётся только при доказанном основании ожидать файл:
существует обычный каталог `Constant/<owner_name>`. Без каталога константы
запись не создаётся. Это основание выбрано по layout #202: у константы
ровно один модуль, и файл лежит прямо в её каталоге.

`missing` означает «файла нет», а не «модуля нет» и не ошибку: в выгрузке
A #202 из 183 каталогов констант файл модуля был у 39, поэтому для
констант `missing` — массовое и ожидаемое состояние. Уровень отчёта для
таких записей (информационный или предупреждающий) — вопрос #208.

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing`
и для `read_error` без чтения байтов оба поля — `None`.

## Владельцы

Владелец — непосредственный подкаталог `Constant/`. Пропускаются без
записей:

- symlink и иные не-обычные каталоги (они не разыменовываются), а также
  каталоги, разрешённый путь которых не лежит непосредственно в
  родительском каталоге;
- файлы в каталоге `Constant/`;
- каталоги, имя которых не является идентификатором 1С или даёт путь,
  недопустимый по `validate_relative_module_path()` (например,
  зарезервированные имена устройств Windows).

Каталог типа сравнивается по точному имени `Constant`; вложенные каталоги
(`ConstantForm/...`, `Ext/`) не обходятся.

## Безопасность

- Сканер только читает: `lstat()`, `iterdir()`, `resolve()` и
  `read_bytes()`. Он не создаёт и не изменяет файлы и не вызывает
  `scan_forms()`, который пишет JSON в каталоги форм.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. `owner_name` содержит реальные имена
  констант: обезличенные отчёты должны агрегировать данные на своём уровне.

## Порядок и повторяемость

Константы обходятся в отсортированном порядке; итоговый порядок задаёт
`ModuleIndex` (OS-нейтральная сортировка по относительному пути), JSON
`module_index/1` детерминирован. Порядок `iterdir()` на результат не влияет.

## Вне scope

Модули объектов, менеджеров, наборов записей и команд (#205–#207), runner,
CLI, post-run report и LLM-контекст (#208), итоговая матрица покрытия
(#209), RAG, разбор BSL и изменения upstream v8unpack.
