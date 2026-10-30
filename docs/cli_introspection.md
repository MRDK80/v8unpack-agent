# Интроспекция CLI-контракта (issue #259)

Машиночитаемый каталог команд для AI-агентов и автоматизации: агент узнаёт
фактический CLI-контракт без разбора README и человеческого `--help`.

## Интерфейс

```bash
v8unpack-agent-run --describe-cli
```

Флаг обрабатывается до разбора аргументов и должен быть единственным
аргументом. Результат — один JSON-объект в одной строке stdout, код 0,
stderr пуст. Выгрузка не читается, файлы не пишутся, вывод не зависит от
текущего каталога, локали и окружения. `--help` не меняется и флаг не
показывает.

### Рассмотренные альтернативы

| Вариант | Решение | Причина |
|---|---|---|
| Глобальный флаг до argparse | выбран | не меняет `--help`, parser и exit codes существующих команд |
| Подкоманда `v8unpack-agent-run describe` | отклонён | у команды позиционный `export_root`: подкоманда изменила бы разбор и help |
| Подкоманда с флагом `--json` | отклонён | смешивает каталог с операционным envelope #258 и его версией |
| Отдельный console script | отклонён | новая точка входа расширяет публичную поверхность сверх задачи |

## Каталог

| Поле | Значение |
|---|---|
| `schema_version` | `"1.0"` — версия формы каталога |
| `kind` | `"v8unpack-agent.cli-catalog"` |
| `json_contract` | `kind` и `schema_version` envelope `--json` ([cli_json](cli_json.md)); версия каталога с ним не смешивается |
| `usage_error` | `exit_code` 2, `source` `argparse` — общая ошибка разбора для всех команд |
| `commands` | конечные формы команд, сортировка по имени |

Запись команды: `name`, `invocation` (argv-префикс), `parameters`,
`side_effects`, `exit_codes`, `json_mode` (`flag` и `error_codes` либо
`null`, если JSON-режима нет).

Параметр: `name`, `kind` (`positional`/`option`), `required`, `type`
(`path`, `string`, `integer`, `number`, `boolean`), `repeatable`, `choices`
(или `null`), `default`. Справочный текст не включается.

Источник истины: параметры строятся обходом фактических parser
`cli.build_parser` и `search_cli.build_parser`; exit codes, побочные эффекты
и значения групп модулей — типизированными реестрами, которые тесты сверяют
с реальными вызовами команд. Код 2 у команд означает только недопустимый
`export_root`; ошибка разбора аргументов описана один раз в `usage_error`.

## Пример для агента

```python
import json
import subprocess

proc = subprocess.run(
    ["v8unpack-agent-run", "--describe-cli"],
    capture_output=True, check=True, text=True, encoding="utf-8",
)
catalog = json.loads(proc.stdout)
assert catalog["schema_version"].split(".")[0] == "1"
run = next(c for c in catalog["commands"] if c["name"] == "v8unpack-agent-run")
required = [p["name"] for p in run["parameters"] if p["required"]]
```

Агент проверяет MAJOR `schema_version`, игнорирует неизвестные поля и не
передаёт в команды пути, которых нет в задаче.

## Совместимость

- Новое необязательное поле, команда или параметр повышают MINOR.
- Удаление или переименование поля, смена типа или смысла — MAJOR,
  отдельной задачей.
- Каталог описывает только публичные команды; внутренний модуль
  `_cli_catalog` публичным API не является.
