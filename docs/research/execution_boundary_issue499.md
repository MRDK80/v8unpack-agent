# R13: детерминированная приёмка границы исполнения

База — roadmap `478b0c1167ccc9c6c065079c4cc2d11c228c7774`, после закрытия
R12 (#498) и её отдельного post-merge CI. Реализация отдельная, старые frozen
источники/контрпример #485 не редактируются. Модельная серия не запускалась:
это проверка полномочий executor, не устойчивости Qwen/Llama.

ADR и таблица угроз/тестов/остаточных ограничений:
[snapshot_report_executor](../snapshot_report_executor.md).
Выбран snapshot-correct на host-owned immutable bytes; current-file-correct
без обеспеченной эксклюзивности не заявляется.

План и контрактные тесты написаны до реализации. Первоначальная коллекция
тестов ожидаемо отказала из-за отсутствующего нового компонента. После
реализации добавлены проверки staging collision, partial-write failure и
точного сохранения опасного литерала как данных. Никаких ответов модели
для настройки защиты, новых acceptance payload или расширения model GO нет.

41 contract/fault test на Linux проходит: реальные synthetic отчёты проверены
с независимыми ожидаемыми значениями. Блокирующие сценарии не создали нового
report, сохранили старый результат и symlink target; повреждённые/частичные
байты не публиковались. Повторный/чужой ticket и batch отклонены. Корректные
exports/query сохранены точно. Directory swap пишет только в закреплённый
inode. Изменения исходника во всех четырёх стадиях оставляют отчёт корректным
для старого явно обозначенного снимка. Ошибки после публикации остаются
committed с реальным cleanup/durability статусом. Исторический внешний writer
по-прежнему демонстрирует currentness-контрпример старого executor.

Это **GO ограниченному reference-компоненту** в документированной Linux/local-FS/
trusted-host модели. Windows/прочие платформы: unsupported до IO, а не прошедшая
проверка Linux-адаптера. Не даёт production/default/model admission, не заявляет
защиту от arbitrary Python/same-uid/root, внешнего writer в приватном output,
hostile FS, crash или downstream instruction laundering. Общие frozen replay,
legacy/CLI и остальные regression входят в полный local/PR/post-merge gate.

Проверки воспроизводятся без LLM/сети:

```bash
python -m pytest -q tests/test_snapshot_execution_issue499.py
ruff check .
mypy
python -m pytest -q
```

Точные final head/base/merge и отдельные CI receipts фиксируются в #499 после
гейтов; их успех не переносится с исторического SHA. Релиз #475 остаётся на паузе.
