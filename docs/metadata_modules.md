# Модули объектов и менеджеров метаданных

Модуль `v8unpack_agent.metadata_modules` обнаруживает и читает объектные и
менеджерские BSL-модули прикладных объектов конфигурации 1С и возвращает
общий контракт `ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md)
(#203). Реализация добавлена в issue #205 эпика #201.

## Доказанные соответствия

Пары взяты только из строк исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
с `owner_kind=metadata_object`, `module_kind` `object` или `manager` и
доказательством `designer_content_match_A`: текст файлов v8unpack совпал с
модулями выгрузки Конфигуратором в файлы.

Файл лежит непосредственно в каталоге объекта:
`<metadata_type>/<owner_name>/<файл>`.

| `metadata_type` | `object` | `manager` |
|---|---|---|
| `AccumulationRegister` | — | `AccumulationRegister.mgr.bsl` |
| `BusinessProcess` | `BusinessProcess.obj.bsl` | — |
| `Catalog` | `Catalog.obj.bsl` | `Catalog.mgr.bsl` |
| `ChartOfCharacteristicType` | `ChartOfCharacteristicType.obj.bsl` | — |
| `DataProcessor` | `DataProcessor.obj.bsl` | `DataProcessor.mgr.bsl` |
| `Document` | `Document.obj.bsl` | `Document.mgr.bsl` |
| `DocumentJournal` | — | `DocumentJournal.mgr.bsl` |
| `Enum` | — | `Enum.obj.bsl` |
| `ExchangePlan` | `ExchangePlan.obj.bsl` | `ExchangePlan.mgr.bsl` |
| `InformationRegister` | — | `InformationRegister.mgr.bsl` |
| `Report` | `Report.obj.bsl` | `Report.mgr.bsl` |
| `Task` | `Task.obj.bsl` | — |

Всего 17 пар. «—» означает, что вид модуля для типа в #202 не доказан, и
сканер такую запись не создаёт.

Суффикс сам по себе не определяет вид модуля. `Enum.obj.bsl` — модуль
менеджера перечисления. `.obj.bsl` регистров сведений и накопления — модуль
набора записей (`record_set`, #206), у констант — `value_manager`, у HTTP- и
Web-сервисов — `service`; эти пары сканер не классифицирует.

### Не поддержано

| Случай | Причина |
|---|---|
| `.obj.bsl` у `ChartOfAccounts`, `ChartOfCalculationTypes`, `AccountingRegister` | в #202 unresolved: только upstream `ext_code` и аналогия layout |
| `Sequences/{Name}/Sequences.obj.bsl` | в #202 unresolved |
| `.mgr.bsl` у `ChartOfCharacteristicType`, `BusinessProcess`, `Task`, `ChartOfAccounts`, `ChartOfCalculationTypes`, `AccountingRegister` | в #202 not_detected |
| `record_set`, `value_manager`, `service`, `command`, `form` | другие виды модулей |
| внешние обработки и отчёты (`ExternalDataProcessor`, `ExternalReport`) | в #202 артефакты не предоставлялись, layout не доказан |
| расширения (`ConfigurationExtension`) | в #202 not_detected, вопрос #209 |
| raw-layout и выгрузка Конфигуратором (`Ext/ObjectModule.bsl`) | исследован только normalized-layout v8unpack |

Модули форм (`<Type>Form/<Форма>/<Type>Form.obj.bsl`,
`DataProcessor/<Имя>/Form/<Форма>/Form.obj.bsl`, `CommonForm/...`) лежат
глубже каталога объекта и объектными модулями не считаются; их по-прежнему
обрабатывает form-pipeline (`scan_forms`).

## API

```python
from pathlib import Path

from v8unpack_agent.metadata_modules import (
    METADATA_OBJECT_MODULE_LAYOUTS,
    scan_metadata_object_modules,
)

index = scan_metadata_object_modules(Path("cf_export"))
print(index.filter(module_kind="manager").to_json())
```

- `METADATA_OBJECT_MODULE_LAYOUTS` — неизменяемое отображение
  `metadata_type → (module_kind → имя файла)` из таблицы выше.
- `scan_metadata_object_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

Поля записей: `owner_kind="metadata_object"`, `metadata_type` — каталог
верхнего уровня, `owner_name` — имя каталога объекта, `module_id` —
`metadata_object:<metadata_type>:<owner_name>:<module_kind>`. Объектный и
менеджерский модули одного владельца — две отдельные записи.

| Состояние | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| каталог типа не читается (`iterdir()`) | `OSError` пробрасывается |
| нет каталогов доказанных типов | пустой `ModuleIndex` |
| два каталога владельца, различающиеся только регистром | `ValueError` (дубликат `module_id` по контракту #203) |

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл на месте модуля |
| `missing` | файла нет по доказанному пути у существующего владельца |

Запись `missing` создаётся только при доказанном основании ожидать файл:
существует обычный каталог `<metadata_type>/<owner_name>` доказанного типа,
и вид модуля применим к этому типу по таблице. Без каталога владельца запись
не создаётся; неприменимый вид модуля записи не создаёт.

`missing` означает «файла нет», а не «модуля нет»: #202 показал, что
v8unpack не записывает часть пустых модулей (шесть пустых модулей объектов
обработок в выгрузке A).

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing` и
для `read_error` без чтения байтов оба поля — `None`.

## Владельцы

Владелец — непосредственный подкаталог каталога типа. Пропускаются без
записей:

- symlink и иные не-обычные каталоги типов и владельцев (они не
  разыменовываются), а также каталоги, разрешённый путь которых не лежит
  непосредственно в родительском каталоге;
- файлы в каталоге типа;
- каталоги, имя которых не является идентификатором 1С или даёт путь,
  недопустимый по `validate_relative_module_path()` (например,
  зарезервированные имена устройств Windows).

## Безопасность

- Сканер только читает: `lstat()`, `iterdir()`, `resolve()` и
  `read_bytes()`. Он не создаёт и не изменяет файлы и не вызывает
  `scan_forms()`, который пишет JSON в каталоги форм.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. `owner_name` содержит реальные имена
  объектов: обезличенные отчёты должны агрегировать данные на своём уровне.

## Порядок и повторяемость

Типы и владельцы обходятся в отсортированном порядке; итоговый порядок задаёт
`ModuleIndex` (OS-нейтральная сортировка по относительному пути), JSON
`module_index/1` детерминирован. Порядок `iterdir()` на результат не влияет.

## Вне scope

Модули наборов записей (#206) и команд (#207), runner, CLI, post-run report
(#208), RAG, разбор BSL и изменения upstream v8unpack.
