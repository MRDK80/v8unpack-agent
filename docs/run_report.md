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
