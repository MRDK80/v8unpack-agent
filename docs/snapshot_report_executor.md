# Snapshot executor: граница исполнения R13 (#499)

Отдельный opt-in reference-компонент:
[`SnapshotExecutor`](../examples/snapshot_report_executor.py). Гарантия —
корректный **отчёт о выбранном снимке**. Это не новый допуск Qwen/Llama,
не OS sandbox и не обещание актуальности изменяемого файла. Старые executor,
validator, схемы и frozen эксперименты не меняются.

## ADR: snapshot-correct

По модели угроз R8 host выбирает операцию, модуль, снимок, выход и запрос.
Снимок — `bytes` внутри frozen `SourceSnapshot`/`ReportContract`, принадлежащих
памяти доверенного host. Executor после захвата **не читает изменяемый путь**:
старый независимый validator извлекает факты из этих bytes. Unsupported до
создания контракта не заменяется полным пустым ответом.

Результат `snapshot_report/1` содержит `correctness: snapshot_only`, request,
operation/module/source/hash, идентичность output directory (device/inode) и
проверенный report. Hash описывает версию/целостность, не автора и не право.
Изменение исходника до/во время/после validation не делает снимок новым.
Для current-file-correct нужна обеспеченная эксклюзивность через commit;
этот адаптер её для внешнего исходника не реализует.

Исторический `external_writer_after_last_check` #485 сохранён как контрпример:
ещё один hash не устраняет окно после последней проверки. Новое решение
меняет предпосылку на host-owned immutable bytes и явно маркирует снимок.

## Proposal → validation → commit

Host создаёт экземпляр на один разрешённый запрос. В нём фиксируются контракт
operation/module/snapshot, новый request ID, заранее открытый приватный output
и единственное имя `report.json`. Dispatcher получает только `execute_calls`,
без shell/network/raw writer. Python-объект host модели не выдаётся.

Вход — host-декодированный JSON batch ровно из одного `save_snapshot_report`,
`arguments={request_id, action}`. Action — прежняя строгая схема ReportContract.
Лишние поля, paths/recipient, чужой request, batch и другой tool отклоняются.
Host обязан строго декодировать JSON, включая duplicate keys/NaN, без выполнения
кода ответа. `proposal()` — детерминированный demo, не успех модели.

`validate`/`commit` — **host API**, не инструменты модели. Validation строит
независимые canonical bytes и непрозрачный in-memory ticket. Принимается только
точная identity текущего ticket этого экземпляра; новая validation инвалидирует
предыдущий. Commit потребляет ticket до IO, успешный commit завершает запрос.
Request ID публичен, сам по себе не capability. Mutation после validation не
меняет сохранённые bytes. После отказа до публикации можно проверить новое
предложение; клиент отдельно ограничивает попытки/время/токены. Допуска R12
это не создаёт.

## Linux effect adapter

Другие платформы (включая Windows) получают `UnsupportedPlatform` **до IO**;
небезопасного path fallback нет. Старые API/CLI остаются кроссплатформенными.
Host выбирает абсолютный output без `..`. Каждый компонент открывается через
`O_DIRECTORY|O_NOFOLLOW` относительно закреплённого родителя. Финальный каталог
принадлежит effective uid host и не имеет group/other permission bits (`0700`
рекомендуется). Предки во время начального выбора, содержимое каталога через
cleanup и Python runtime контролирует доверенный host.

Все дальнейшие операции привязаны к dirfd. Rename/symlink-подмена имени после
open не перенаправляет запись: она остаётся в исходном inode. Доступность по
старому pathname не обещана. Произвольная запись другим процессом того же uid,
root, смена mount/permissions и компрометация host вне этой модели угроз.

Случайный staging создаётся `O_EXCL|O_NOFOLLOW`, mode `0600`. Write-loop
обрабатывает partial/zero write, затем fsync файла. Link между фиксированными
basename в одном dirfd атомарно публикует полные bytes без перезаписи прежнего
файла/symlink/каталога. Старый валидный отчёт сохраняется побайтно. Staging —
разрешённый внутренний эффект в приватном output, не опубликованный отчёт.

До link ошибка означает blocked без нового report. Cleanup failure возвращает
`cleanup: failed`; приватный staging может остаться, host обслуживает каталог.
После link статус **committed** даже при cleanup/close/fsync-directory failure.
`durability: unknown` не скрывает эффект; `synced` означает успешные fsync,
не абсолютную гарантию исправности носителя. Используется локальная FS с обычной
Linux-семантикой; hostile/network FS и crash recovery вне контракта.

Первичные источники:
[Python 3.10 os/dir_fd](https://docs.python.org/3.10/library/os.html#files-and-directories),
[Linux open/openat](https://man7.org/linux/man-pages/man2/open.2.html),
[link/linkat](https://man7.org/linux/man-pages/man2/link.2.html),
[fsync](https://man7.org/linux/man-pages/man2/fsync.2.html).

## Угроза → механизм → тест → остаточное ограничение

Реальные synthetic файлы, без LLM/сети:
`tests/test_snapshot_execution_issue499.py`. Названия тестов в таблице сокращены.

| Угроза | Механизм | Тест | Ограничение |
|---|---|---|---|
| Подмена facts/module/operation/source/report | Независимый validator на host bytes | `malicious_proposals` | Покрытая BSL-грамматика, две операции |
| Чужой request/output/replay | Request + ticket identity + один commit | `ticket_is_host_identity`, `concurrent_commit_once` | Новый host grant — новое разрешение |
| Mutation после validation | Canonical bytes отдельно от proposal | `mutation_after_validation` | Доверенный Python runtime |
| Batch/direct writer/path/summary promotion | Единственный строгий dispatcher | `malicious_proposals` | Host не регистрирует обходной writer |
| Stale до захвата | Indexed-byte contract fail closed | `source_change_before_capture` | Currentness после захвата не обещана |
| Смена source до/во время/после проверки/commit | Immutable bytes + snapshot-only envelope | `external_source_change` | Текущий файл может отличаться |
| Symlink/traversal | Покомпонентный no-follow open | `symlink_rejected`, `absolute_path_required` | Доверенное начальное host-разрешение |
| Directory swap после open | Закреплённый dirfd | `directory_swap` | Старое имя может стать недоступно |
| Старый report/symlink target | Atomic no-clobber link | `no_clobber` | Приватный output на запрос |
| IO/partial/zero write | Staging до публикации | `prepublication_failure`, `partial_write` | IO denial срывает полезную задачу |
| Cleanup/sync failure | Фактический статус, commit terminal | `cleanup_failure`, `directory_sync_failure` | Staging residue/unknown durability |
| Закрытый/recycled fd | close инвалидирует fd/ticket | `closed_host` | Host использует context manager |
| Неподдержанная платформа | Отказ до IO | `unsupported_platform` | Linux-only adapter |
| Старый внешний writer после проверки | Контрпример #485 сохранён | `historical_external_writer` | Не объявляется решённой currentness-гонкой |

Private method, отдельный процесс, nonce и hash не дают OS-изоляцию. Не выдавайте
модели arbitrary Python или writer под uid host. Downstream-агент, исполняющий
правильный опасный литерал, относится к R15 (#501): проверка истинности не делает
данные инструкциями. Все значения отчёта остаются недоверенными.

## Пример host

```python
from examples.snapshot_report_executor import SnapshotExecutor

# contract выбран host через create_report_contract; output_dir — отдельный
# существующий приватный каталог. В реальном клиенте batch декодирует host.
with SnapshotExecutor(contract, output_dir=output_dir) as executor:
    batch = [{"name": "save_snapshot_report", "arguments": executor.proposal()}]
    outcome = executor.execute_calls(batch)  # deterministic demo, без LLM
```

Новый компонент не подменяет старые frozen клиенты и не снимает utility NO-GO
R11/R12. Приёмка исполнения детерминированная; новый профиль модели потребует
отдельной зафиксированной оценки.
