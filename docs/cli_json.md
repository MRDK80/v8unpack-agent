# JSON-режим CLI (issue #258)

Решение о машинном выводе `v8unpack-agent-run` для автоматизации и
AI-агентов: форма envelope, коды ошибок, версионирование и совместимость.

## Область

| Команда | JSON-режим | Причина |
|---|---|---|
| `v8unpack-agent-run` | флаг `--json` | единственный console script пакета |
| `python -m v8unpack_agent.search_cli build/query` | не добавляется | stdout уже один канонический JSON схемы `search_cli/1` ([search_cli](search_cli.md)); второй envelope сломал бы действующий контракт |

`--help` не переводится в JSON: справка остаётся текстом с кодом 0.

## Включение

Режим включается только явным флагом `--json`; автоопределение TTY не
используется. Без флага stdout, stderr, `--help` (кроме строки нового флага)
и exit codes не меняются.

## Потоки

- stdout — ровно один JSON-объект в одной строке с завершающим `\n`.
  Текстовая сводка в JSON-режиме не печатается.
- stderr — прежние однострочные диагностические сообщения (ошибки
  аргументов argparse, корня, записи отчёта, фатальная ошибка, внутренняя
  ошибка). Машинный вывод с ними не смешивается.
- JSON-отчёт по `--report-path` пишется как раньше; его формат не меняется
  ([run_report](run_report.md)).

## Envelope

Ключи сериализуются с сортировкой (`sort_keys`), `ensure_ascii=False`:
кириллица в кодах и счётчиках не встречается, а UTF-8 потоков задан CLI.

| Поле | Тип | Значение |
|---|---|---|
| `schema_version` | string | `"1.0"` |
| `kind` | string | `"v8unpack-agent-run.result"` |
| `ok` | bool | `true` только при exit code 0 |
| `status` | string | `ok`, `bad_input`, `degraded`, `fatal`, `write_error`, `fatal_and_write_error` |
| `exit_code` | int | совпадает с кодом процесса |
| `error` | object \| null | `{"code": string}`; `null` при 0 и 3 |
| `data` | object \| null | результат прогона; `null`, если прогон не начинался |

`data`:

| Поле | Тип | Значение |
|---|---|---|
| `completed` | bool | прогон завершён без фатальной ошибки |
| `degraded` | bool | есть `partial` или `failed` объекты |
| `summary` | object | `found`, `complete`, `partial`, `failed`, `excluded` (int) |
| `prompt_chars` | int | суммарная длина промпт-фрагментов |
| `scan_warnings` | int | число предупреждений сканера уровня прогона |
| `fatal_error` | object \| null | `reason_code`, `error_type` (string) |
| `report` | object | `file_name` (string), `written` (bool), `schema_version` (int) |

`report.file_name` — только последний сегмент `--report-path` по `pathlib`;
каталоги, абсолютные пути и содержимое модулей в envelope не попадают.
Временные метки не выводятся, поэтому повторный запуск на тех же данных
даёт байтово тот же envelope.

## Коды ошибок

Код выбирается по месту отказа и типу исключения, а не по тексту сообщения.
Реестр — `cli.JSON_ERROR_CODES`; каждый код достижим и покрыт тестом.

| `error.code` | exit code | Условие |
|---|---:|---|
| `arguments_invalid` | 2 | ошибка разбора аргументов argparse или групп модулей |
| `export_root_invalid` | 2 | `export_root` не существует или не каталог |
| `pipeline_fatal` | 4 | управляемая фатальная ошибка; детали — `data.fatal_error` |
| `report_write_failed` | 5 | отчёт не записан |
| `pipeline_fatal_and_report_write_failed` | 6 | оба отказа одновременно |
| `internal_error` | 4 | непредвиденное исключение; `data` равен `null` |

Exit codes не меняются и новых не вводится ([runner](runner.md)).

## Версионирование и совместимость

- `schema_version` — `MAJOR.MINOR`. Добавление необязательного поля в
  `data` или нового кода ошибки повышает MINOR; потребитель обязан
  игнорировать неизвестные поля.
- Удаление или переименование поля, смена типа, смысла `ok`/`status` или
  соответствия кода ошибки exit code повышает MAJOR и согласуется отдельной
  задачей.
- `kind` не меняется в пределах MAJOR.
