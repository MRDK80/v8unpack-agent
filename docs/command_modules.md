# Модули команд

Модуль `v8unpack_agent.command_modules` обнаруживает и читает BSL-модули
общих команд и команд прикладных объектов конфигурации 1С и возвращает
общий контракт `ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md)
(#203) с `module_kind="command"`. Реализация добавлена в issue #207 эпика
#201.

## Доказанные классы команд

Классы взяты только из строк исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
с `module_kind=command` и доказательством `designer_content_match_A`:
текст файлов v8unpack совпал с `Ext/CommandModule.bsl` выгрузки
Конфигуратором в файлы. Все шесть строк #202 с `module_kind=command` имеют
это доказательство; других классов команд исследование не обнаружило.

| Класс | `owner_kind` | `metadata_type` | Относительный путь модуля |
|---|---|---|---|
| общая команда | `common_command` | `CommonCommand` | `CommonCommand/<Команда>/CommonCommand.obj.bsl` |
| команда справочника | `metadata_object_command` | `Catalog` | `Catalog/<Объект>/CatalogCommand/<Команда>/CatalogCommand.obj.bsl` |
| команда обработки | `metadata_object_command` | `DataProcessor` | `DataProcessor/<Объект>/DataProcessorCommand/<Команда>/DataProcessorCommand.obj.bsl` |
| команда документа | `metadata_object_command` | `Document` | `Document/<Объект>/DocumentCommand/<Команда>/DocumentCommand.obj.bsl` |
| команда регистра сведений | `metadata_object_command` | `InformationRegister` | `InformationRegister/<Объект>/InformationRegisterCommand/<Команда>/InformationRegisterCommand.obj.bsl` |
| команда отчёта | `metadata_object_command` | `Report` | `Report/<Объект>/ReportCommand/<Команда>/ReportCommand.obj.bsl` |

Вид владельца и вид модуля различаются: `module_kind` у всех записей —
`command`, а `owner_kind` показывает, общая это команда или команда
объекта. Общие команды — команды уровня конфигурации, но их `owner_kind` —
`common_command`, а не `configuration`: так зафиксировано в #202 и #203.

### Не поддержано

| Случай | Причина |
|---|---|
| команды других типов (`BusinessProcess`, `Task`, `ExchangePlan`, `Enum`, `AccumulationRegister`, `ChartOfCharacteristicType`, ...) | в #202 файлы `<Type>Command.obj.bsl` не встретились ни в одной выгрузке; классифицировать догадкой нельзя |
| расширения, внешние обработки и отчёты | в #202 not_detected |
| raw-layout и выгрузка Конфигуратором (`Commands/<Команда>/Ext/CommandModule.bsl`, `CommonCommands/...`) | исследован только normalized-layout v8unpack |
| контейнер команд с чужим именем (`Catalog/<Объект>/DocumentCommand/...`) или в другом регистре | не соответствует доказанному layout |

Недоказанные случаи не дают записей, в том числе `missing`. Повышение их
до поддержанных требует новых доказательств и отдельного решения.

## API

```python
from pathlib import Path

from v8unpack_agent.command_modules import (
    COMMON_COMMAND_MODULE_FILE,
    COMMON_COMMAND_TYPE,
    OBJECT_COMMAND_CONTAINERS,
    scan_command_modules,
)

index = scan_command_modules(Path("cf_export"))
print(index.filter(owner_kind="metadata_object_command").to_json())
```

- `COMMON_COMMAND_TYPE` — `"CommonCommand"`: `metadata_type` общей команды
  и имя её каталога верхнего уровня.
- `COMMON_COMMAND_MODULE_FILE` — `"CommonCommand.obj.bsl"`.
- `OBJECT_COMMAND_CONTAINERS` — неизменяемое отображение
  `metadata_type → каталог команд объекта` (`Catalog → CatalogCommand`,
  ...). Имя файла модуля — `<контейнер>.obj.bsl`.
- `scan_command_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

| Состояние | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| каталог типа, объекта или контейнера не читается (`iterdir()`) | `OSError` пробрасывается |
| нет каталогов доказанных классов | пустой `ModuleIndex` |
| два каталога команды одного владельца, различающиеся только регистром букв | `ValueError` (дубликат `module_id` по контракту #203) |

Политика ошибок совпадает со сканерами #204–#206.

## Идентичность команд

`module_id` строится по формуле #203
`{owner_kind}:{metadata_type}:{owner_name}:{module_kind}` и не зависит от
пути. `owner_name` общей команды — её имя; команды объекта —
`<Объект>.<Команда>`, как задано для команд в `docs/modules.md`.

| Команда | `module_id` |
|---|---|
| `CommonCommand/Run/...` | `common_command:CommonCommand:Run:command` |
| `Catalog/Alpha/CatalogCommand/Run/...` | `metadata_object_command:Catalog:Alpha.Run:command` |
| `Catalog/Beta/CatalogCommand/Run/...` | `metadata_object_command:Catalog:Beta.Run:command` |
| `Document/Alpha/DocumentCommand/Run/...` | `metadata_object_command:Document:Alpha.Run:command` |

Одноимённые команды разных объектов одного типа различаются именем
объекта внутри `owner_name`; разных типов — `metadata_type`; общая и
объектная — `owner_kind`. Записи команд не пересекаются с `object`,
`manager`, `record_set` и модулями уровня конфигурации того же владельца,
поэтому индексы сканеров #204–#207 объединяются через
`ModuleIndex.from_entries` без конфликтов. Схема #203 и формула
`module_id` не менялись.

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл на месте модуля |
| `missing` | файла нет по доказанному пути у существующего каталога команды |

Основание ожидать файл для команды — существование обычного каталога
самой команды доказанного класса: `CommonCommand/<Команда>` либо
`<metadata_type>/<Объект>/<контейнер>/<Команда>`, где и каталог объекта,
и контейнер — обычные каталоги. Каталог объекта без контейнера и пустой
контейнер без каталогов команд записей не создают: у объекта команд может
не быть, и это не отсутствие файла. Правило каталога объекта из #205 на
команды механически не переносится.

`missing` означает «файла нет», а не «модуля нет»: #202 показал, что
v8unpack не записывает часть пустых модулей. Метаданные команды без BSL
не считаются ошибкой; запись получает статус `missing`, а не
`read_error`. Отдельный предупреждающий уровень для допустимого
отсутствия BSL в #207 не вводится — это политика отчёта #208.

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing`
и для `read_error` без чтения байтов оба поля — `None`.

## Владельцы

Каталоги обходятся на трёх уровнях: тип → объект → контейнер → команда
(для общих команд — тип → команда). На каждом уровне пропускаются без
записей:

- symlink и иные не-обычные каталоги (они не разыменовываются), а также
  каталоги, разрешённый путь которых не лежит непосредственно в
  родительском каталоге;
- файлы вместо каталогов;
- каталоги, имя которых не является идентификатором 1С или даёт путь,
  недопустимый по `validate_relative_module_path()` (например,
  зарезервированные имена устройств Windows).

Каталоги форм (`<Type>Form/...`, `Form/...`), `Ext/`, модули объекта и
менеджера в каталоге объекта и `CommonForm/`, `CommonModule/` не
рассматриваются как команды.

## Безопасность

- Сканер только читает: `lstat()`, `iterdir()`, `resolve()` и
  `read_bytes()`. Он не создаёт и не изменяет файлы и не вызывает
  `scan_forms()`, который пишет JSON в каталоги форм.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. `owner_name` содержит реальные имена
  объектов и команд: обезличенные отчёты должны агрегировать данные на
  своём уровне.

## Порядок и повторяемость

Типы, объекты и команды обходятся в отсортированном порядке; итоговый
порядок задаёт `ModuleIndex` (OS-нейтральная сортировка по относительному
пути), JSON `module_index/1` детерминирован. Порядок `iterdir()` на
результат не влияет.

## Вне scope

Исполнение команд и анализ обработчиков, модули объектов, менеджеров и
наборов записей (#205, #206), менеджеров значений и сервисов (#337),
runner, CLI, post-run report (#208), RAG, разбор BSL и изменения upstream
v8unpack.
