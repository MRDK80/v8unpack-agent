# Post-run report

`v8unpack_agent.run_report` задаёт машиночитаемый итог управляемого запуска и атомарную запись JSON. Модуль реализует библиотечный контракт issue #196; production-runner и CLI относятся к issue #198.

## Граница ответственности

Модель поддерживает три вида единиц обработки:

- `form`;
- `common_module`;
- `skd_artifact`.

Статусы результата: `complete`, `partial`, `failed`, `excluded`. Объекты со статусом `excluded` учитываются отдельно и не входят в `found`.

Обязательный инвариант:

```text
found = complete + partial + failed
discovered = found + excluded
```

`partial` и `failed` требуют `stage` и `reason_code`. `complete` не допускает полей деградации. `excluded` требует явный `reason_code`.

Запуск пайплайна, выбор стадий и причин деградации, запись отчёта на диск и отображение итога в exit code относятся к production runner и CLI — см. [docs/runner.md](runner.md). Сам модуль `run_report` ничего не запускает и код возврата не выбирает.

## Пример

```python
from pathlib import Path

from v8unpack_agent.run_report import (
    ObjectRunResult,
    PostRunReport,
    RunObjectKind,
    RunObjectStatus,
    RunSummary,
    write_post_run_report,
)

objects = (
    ObjectRunResult(
        object="Document/Example/Form/Main",
        object_kind=RunObjectKind.FORM,
        status=RunObjectStatus.COMPLETE,
    ),
)
report = PostRunReport(
    schema_version=1,
    completed=True,
    started_at="2026-01-01T00:00:00Z",
    finished_at="2026-01-01T00:00:01Z",
    summary=RunSummary.from_objects(objects),
    objects=objects,
)
write_post_run_report(report, Path("post-run.json"))
```

Родительский каталог target должен существовать. Writer создаёт временный файл в том же каталоге, записывает UTF-8, выполняет `os.replace()` и по возможности удаляет временный файл при ошибке. Если замена не состоялась, существующий target сохраняется.

## Детерминированность

Перед сериализацией объекты сортируются по виду, логическому имени, статусу, стадии и причине. JSON формируется с фиксированными параметрами и завершается переводом строки.

## Безопасность данных

Модель отклоняет:

- абсолютные POSIX- и Windows-пути;
- многострочные сообщения;
- traceback-маркеры;
- некорректные машинные коды.

В отчёт нельзя передавать исходный BSL/JSON, строки подключения, имена хостов и другие чувствительные значения. Реальные отчёты запуска не должны коммититься.

## Существующие классификации

`unindexed_reason_code()` и `decode_error_reason_code()` возвращают исходные значения существующих enum без создания копий. `scan_warning_reason_code()` делегирует разбор канонической функции `scan_warning_code()`. Для CommonModule и SKD предусмотрено явное отображение доказанных статусов в общий результат.

Коды предупреждений сканера публикуются в верхнем регистре, а модель требует нижний. `scan_warning_reason_code()` регистр не меняет: нормализация и fallback-код для legacy-предупреждений без маркера выполняются на стороне runner (#198).

Корневой `v8unpack_agent.__init__` намеренно не расширяется: модуль импортируется напрямую, чтобы не менять контракт ленивых импортов до завершения #140.

## Сериализованный JSON-файл

Конструктор `PostRunReport` и layout готового файла — два разных уровня.

Форма конструктора Python и layout JSON-файла не идентичны по вложенности.

Это намеренный контракт сериализатора, а не расхождение модели: `to_dict()`
группирует метаданные запуска в объект `run`, тогда как конструктор принимает
`completed`, `started_at` и `finished_at` плоским списком аргументов.

### Python model

```python
report = PostRunReport(
    schema_version=1,
    completed=True,
    started_at="2026-01-01T00:00:00Z",
    finished_at="2026-01-01T00:00:01Z",
    summary=summary,
    objects=(),
)
```

### Пример сериализованного файла

`write_post_run_report()` пишет детерминированный JSON: `ensure_ascii=False`,
`indent=2`, `sort_keys=True` и завершающий перевод строки. Порядок ключей
алфавитный и стабилен между запусками.

```json
{
  "fatal_error": null,
  "objects": [],
  "run": {
    "completed": true,
    "finished_at": "2026-01-01T00:00:01Z",
    "started_at": "2026-01-01T00:00:00Z"
  },
  "schema_version": 1,
  "summary": {
    "complete": 0,
    "discovered": 0,
    "excluded": 0,
    "failed": 0,
    "found": 0,
    "partial": 0
  }
}
```

### Ключи верхнего уровня

| Ключ | Назначение |
|------|------------|
| `schema_version` | Версия схемы файла отчёта. |
| `run` | Состояние и времена управляемого запуска. |
| `summary` | Счётчики результата прогона. |
| `objects` | Детализированные результаты по объектам. |
| `fatal_error` | Санитизированная run-level ошибка либо `null`. |

### Объект run

| Поле | Значение |
|------|----------|
| `run.completed` | Runner завершился управляемо. |
| `run.started_at` | Время старта прогона. |
| `run.finished_at` | Время завершения прогона. |

`run.completed` равно `true` при управляемом завершении runner. Это не
синоним кода возврата 0 и не утверждение об отсутствии `partial` или
`failed` объектов.

### Инварианты summary

```text
found = complete + partial + failed
discovered = found + excluded
```

Для пустого success-отчёта обе суммы равны нулю.

Поля конструктора `RunSummary` — `found`, `complete`, `partial`, `failed` и `excluded`. Ключ `discovered` в файле вычисляется моделью как `found + excluded` и не передаётся в конструктор.

### Degraded-завершение

При degraded-прогоне отчёт остаётся полным:

```text
код возврата CLI: 3
run.completed: true
fatal_error: null
summary.partial > 0 либо summary.failed > 0
```

Код 3 означает пригодный, но неполный результат, а не управляемый fatal.

### Managed fatal

```text
run.completed: false
fatal_error: объект
код возврата CLI: 4
```

Политика кодов возврата описана в [runner.md](runner.md).

### Чтение файла

```python
import json
from pathlib import Path

payload = json.loads(Path("post-run.json").read_text(encoding="utf-8"))
completed = payload["run"]["completed"]
```

Обращение к `completed` на верхнем уровне приводит к `KeyError`: поле
находится внутри объекта `run`.
