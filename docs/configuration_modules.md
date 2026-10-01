# Модули уровня конфигурации

Модуль `v8unpack_agent.configuration_modules` обнаруживает и читает четыре
глобальных BSL-модуля конфигурации 1С и возвращает общий контракт
`ModuleIndex` / `ModuleEntry` из [`docs/modules.md`](modules.md) (#203).
Реализация добавлена в issue #204 эпика #201.

## Доказанные соответствия

Соответствия взяты только из строк исследования #202
([`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
со статусом доказательства `designer_exact_match_A`: текст файла v8unpack
точно совпал с модулем выгрузки Конфигуратором в файлы.

| `module_kind` | Файл в корне выгрузки | Модуль Конфигуратора |
|---|---|---|
| `ordinary_application` | `Configuration.802.bsl` | `Ext/OrdinaryApplicationModule.bsl` |
| `managed_application` | `Configuration.app.bsl` | `Ext/ManagedApplicationModule.bsl` |
| `external_connection` | `Configuration.con.bsl` | `Ext/ExternalConnectionModule.bsl` |
| `session` | `Configuration.seance.bsl` | `Ext/SessionModule.bsl` |

Файлы лежат непосредственно в корне выгрузки, без каталога
`Configuration/`. Для всех записей `owner_kind="configuration"`,
`metadata_type=None`, `owner_name=None`, `module_id` —
`configuration:::<module_kind>`.

Поддержан только normalized-layout v8unpack: #202 исследовал только его,
raw-layout там явно вне scope. Модули расширения (`ConfigurationExtension`,
upstream-суффиксы `app` / `ssn` / `con`) в #202 имеют статус
`not_detected` и сканером не выдаются. Любые другие файлы
`Configuration.*.bsl` и `ConfigurationExtension.*.bsl` не классифицируются.

## API

```python
from pathlib import Path

from v8unpack_agent.configuration_modules import (
    CONFIGURATION_MODULE_FILES,
    scan_configuration_modules,
)

index = scan_configuration_modules(Path("cf_export"))
print(index.to_json())
```

- `CONFIGURATION_MODULE_FILES` — неизменяемое отображение
  `module_kind → имя файла` из таблицы выше.
- `scan_configuration_modules(root: Path) -> ModuleIndex`.

Корневой `v8unpack_agent.__init__` не расширяется.

| Состояние корня | Результат |
|---|---|
| корень отсутствует | `FileNotFoundError` |
| корень не каталог | `NotADirectoryError` |
| нет ни одного из четырёх файлов | пустой `ModuleIndex` |
| есть хотя бы один из четырёх файлов | ровно четыре записи |

## Статусы и правило missing

| Статус | Когда |
|---|---|
| `ok` / `empty` / `whitespace_only` | по `classify_bsl_bytes()` для прочитанных байтов |
| `read_error` | невалидный UTF-8, `OSError`, symlink, каталог или иной не-обычный файл |
| `missing` | файла нет по доказанному пути |

`missing` означает «файла нет», а не «модуля нет». Запись `missing`
создаётся только если корень опознан как выгрузка конфигурации — в нём есть
хотя бы один из четырёх доказанных файлов. Основание ожидать остальные
файлы: путь каждого из четырёх модулей доказан в #202, а модули уровня
конфигурации — фиксированный набор свойств конфигурации. В #202 у выгрузки B
нет `Configuration.802.bsl`; сканер выдаёт для неё `missing`, не утверждая,
пуст модуль или отсутствует.

Для прочитанных файлов заполняются `size_bytes` и `sha256`; для `missing` и
для `read_error` без чтения байтов (symlink, каталог, `OSError`) оба поля —
`None`.

## Безопасность

- Сканер только читает: `lstat()` и `read_bytes()`. Он не создаёт и не
  изменяет файлы и не вызывает `scan_forms()`, который пишет JSON в каталоги
  форм.
- Symlink на месте модуля не разыменовывается. Дополнительно проверяется, что
  разрешённый путь файла лежит непосредственно в разрешённом корне.
- Пути в индексе — относительные POSIX-строки, проверенные
  `validate_relative_module_path()` по семантике POSIX и Windows.
- BSL-текст в индекс не попадает. Имена владельцев у модулей конфигурации
  отсутствуют (`None`), поэтому дополнительного обезличивания индекс не
  требует.

## Порядок и повторяемость

Порядок записей задаёт `ModuleIndex` (OS-нейтральная сортировка по
относительному пути), JSON `module_index/1` детерминирован. Повторный scan
неизменной выгрузки даёт идентичный индекс.

## Вне scope

Объектные, менеджерские, record-set и command-модули (#205–#207), runner,
CLI, post-run report, RAG, разбор BSL и изменения upstream v8unpack.
