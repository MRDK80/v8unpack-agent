# Модули наборов записей регистров

Модуль `v8unpack_agent.record_set_modules` обнаруживает и читает BSL-модули
наборов записей регистров конфигурации 1С и возвращает общий контракт
`ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md) (#203) с
`module_kind="record_set"`. Реализация добавлена в issue #206 эпика #201.

## Доказанные соответствия

Типы регистров взяты только из строк исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
с `owner_kind=metadata_object`, `module_kind=record_set` и доказательством
`designer_content_match_A`: текст файлов v8unpack совпал с
`RecordSetModule.bsl` выгрузки Конфигуратором в файлы.

Файл лежит непосредственно в каталоге регистра:
`<metadata_type>/<owner_name>/<файл>`.

| `metadata_type` | Файл модуля набора записей | Модуль Конфигуратора |
|---|---|---|
| `AccumulationRegister` | `AccumulationRegister.obj.bsl` | `AccumulationRegisters/{Name}/Ext/RecordSetModule.bsl` |
| `InformationRegister` | `InformationRegister.obj.bsl` | `InformationRegisters/{Name}/Ext/RecordSetModule.bsl` |

Суффикс `obj` сам по себе не определяет вид модуля: у справочников и
документов это модуль объекта, у перечислений — модуль менеджера, у
констант — модуль менеджера значения. Здесь `record_set` задаёт пара
«доказанный тип регистра + имя файла». Модули менеджеров тех же регистров
(`.mgr.bsl`) выдаёт сканер #205 ([`docs/metadata_modules.md`](metadata_modules.md));
`METADATA_OBJECT_MODULE_LAYOUTS` ради `record_set` не расширяется.

### Не поддержано

| Случай | Причина |
|---|---|
| `AccountingRegister/{Name}/AccountingRegister.obj.bsl` | в #202 unresolved: только upstream `ext_code` и аналогия layout, с Конфигуратором не сверено |
| `Sequences/{Name}/Sequences.obj.bsl` | в #202 unresolved: семантика не установлена |
| `CalculationRegister` | в #202 not_detected: каталогов нет ни в одной выгрузке |
| модули менеджеров, форм и команд регистров | другие виды модулей (#205, form-pipeline, #207) |
| расширения, внешние обработки и отчёты | в #202 not_detected |
| raw-layout и выгрузка Конфигуратором (`Ext/RecordSetModule.bsl`) | исследован только normalized-layout v8unpack |

Unresolved-случаи не классифицируются догадкой и не дают записей, в том
числе `missing`. Повышение их до поддержанных требует новых доказательств и
отдельного решения.

## API

```python
from pathlib import Path

from v8unpack_agent.record_set_modules import (
    RECORD_SET_MODULE_FILES,
    scan_record_set_modules,
)

index = scan_record_set_modules(Path("cf_export"))
print(index.filter(read_status="missing").to_json())
```

- `RECORD_SET_MODULE_FILES` — неизменяемое отображение
  `metadata_type → имя файла` из таблицы выше.
- `scan_record_set_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

Поля записей: `module_kind="record_set"`, `owner_kind="metadata_object"`,
`metadata_type` — каталог верхнего уровня, `owner_name` — имя каталога
регистра, `module_id` —
`metadata_object:<metadata_type>:<owner_name>:record_set`. Идентификатор не
зависит от пути и не пересекается с `...:manager` того же регистра из #205,
поэтому индексы двух сканеров объединяются без конфликтов.

| Состояние | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| каталог типа не читается (`iterdir()`) | `OSError` пробрасывается |
| нет каталогов доказанных типов | пустой `ModuleIndex` |
| два каталога регистра, различающиеся только регистром букв | `ValueError` (дубликат `module_id` по контракту #203) |

Политика ошибок совпадает со сканерами #204 и #205.

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл на месте модуля |
| `missing` | файла нет по доказанному пути у существующего регистра |

Запись `missing` создаётся только при доказанном основании ожидать файл:
существует обычный каталог `<metadata_type>/<owner_name>` доказанного типа
регистра, к которому `record_set` применим по матрице #202. Без каталога
регистра запись не создаётся; неприменимые и unresolved типы записей не
создают.

`missing` означает «файла нет», а не «модуля нет»: #202 показал, что
v8unpack не записывает часть пустых модулей, а у многих регистров модуля
набора записей нет вовсе.

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing` и
для `read_error` без чтения байтов оба поля — `None`.

## Владельцы

Владелец — непосредственный подкаталог каталога типа. Пропускаются без
записей:

- symlink и иные не-обычные каталоги типов и регистров (они не
  разыменовываются), а также каталоги, разрешённый путь которых не лежит
  непосредственно в родительском каталоге;
- файлы в каталоге типа;
- каталоги, имя которых не является идентификатором 1С или даёт путь,
  недопустимый по `validate_relative_module_path()` (например,
  зарезервированные имена устройств Windows).

Вложенные каталоги форм (`<Type>Form/...`), команд (`<Type>Command/...`) и
`Ext/` не обходятся.

## Безопасность

- Сканер только читает: `lstat()`, `iterdir()`, `resolve()` и
  `read_bytes()`. Он не создаёт и не изменяет файлы и не вызывает
  `scan_forms()`, который пишет JSON в каталоги форм.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. `owner_name` содержит реальные имена
  регистров: обезличенные отчёты должны агрегировать данные на своём уровне.

## Порядок и повторяемость

Типы и регистры обходятся в отсортированном порядке; итоговый порядок
задаёт `ModuleIndex` (OS-нейтральная сортировка по относительному пути), JSON
`module_index/1` детерминирован. Порядок `iterdir()` на результат не влияет.

## Вне scope

Модули объектов и менеджеров (#205), команд (#207), менеджеров значений и
сервисов (#337), runner, CLI, post-run report (#208), RAG, разбор BSL и
изменения upstream v8unpack.
