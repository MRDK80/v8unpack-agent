# Ограниченное восстановление JSON-агента

`examples.bounded_fact_agent` — отдельный opt-in research controller R12.
Старый reference-клиент и его default/исторические NO-GO не меняются.
Host заранее разрешает конкретный `TaskEvidence`, уникальный scope и отдельный
output. `BoundedRecovery(..., calibration=False, admitted=False)` по умолчанию
не вызывает модель: admission — явное решение host по проверенному profile/task.
`calibration=True` используется для разработки, не означает production GO.

```python
from examples.bounded_fact_agent import BoundedRecovery, TimedLocalOllama

backend = TimedLocalOllama()
backend.verify("qwen25")
agent = BoundedRecovery(evidence, output_dir=output, scope_id="request_demo",
                        profile="qwen25", seed=17, calibration=True)
result = agent.run(backend.chat)
```

Без вызова `run` импорт ничего не пишет и не вызывает модель. Вход должен
быть полным; неизвестная/усечённая проекция не создаёт controller.
Все исходные имена/литералы и ответы модели остаются недоверенными данными.

## Политика, заданная до ответов

R8 разрешает2 recovery attempts: первое предложение + максимум2 повтора,
всего3 RPC. `RecoveryPolicy` принимает только конечные host лимиты:
`max_attempts`1…3, `total_seconds`(0,540], `total_generated_tokens`0…
max_attempts×profile.num_predict. Если последнее не задано, используется этот
предел. Температура.3, context8192,384/4096tokens на запрос остаются прежними.
Модель не задаёт бюджеты в своих аргументах.

FSM: ready → proposal → validate → committed. Отказ → retry с фиксированным
feedback и повторным чтением **того же** scope → новая proposal. Лимит,
timeout, ошибка host/протокола runtime или смена снимка → terminal no_report.
Повтор `run` terminal экземпляра возвращает копию уже сохранённого результата,
не вызывает модель/writer и не является новым выполнением.

Только строгий JSON по прежней схеме scope_id/fact_ids. Prose/duplicate keys/
NaN не извлекаются и не исправляются. Native tool calls/batch не исполняются;
fallback на native/другой профиль отсутствует. Сохраняются все реальные
requests/responses/events, включая невалидные предложения.

Feedback содержит только whitelist кодов, постоянные пути полей
`evidence.facts[*].ref`/`scope_id`, оставшееся число попыток и исходную scoped
опись. Ошибка/приватный exception/source payload не вставляются в control-поля,
system или developer. Повторные имена/литералы остаются в data message.
Это уточнение интерфейса, не скрытый test expected/attacker target.

Длинные fact_id не превращаются автоматически в короткие ref. Неполный набор
не дополняется. Только **новое валидное предложение модели** допускается
к прежнему независимому validator/ReportExecutor. Материализацию отчёта
выполняет host; вычисление статических фактов не приписывается LLM.

## Снимок и бюджеты

Перед каждым RPC/допуском в writer host перепроверяет индексированные байты
TaskEvidence. При смене snapshot старый request аннулируется, новая версия
не подмешивается; нужен новый host request. `scoped_evidence()` читает только
этот выбранный модуль, не принимает путь/новый module id от модели.

Deadline ограничивает допуск новых RPC и валидируемого эффекта: проверяется
до/после RPC и перед передачей в validator/writer. Transport socket timeout
не превышает180seconds и оставшегося admission времени. Это не жёсткий
wall-clock interrupt всего HTTP/уже начатой файловой транзакции и не остановка
GPU/OS. Локальный runtime и FS доверены. Границы exclusivity/TOCTOU старого
[executor](action_validation.md) остаются; OS sandbox не добавлен.

Даже поздний ответ учитывается в reported tokens, но после deadline не
допускается к записи. При timeout/неполных counters `usage_complete=False`:
неизвестный расход не объявляется нулевым. Real full-trial latency записывается
отдельно от относительных clock samples, которые воспроизводят **решения**
бюджетного controller в offline replay.

## Проверка и область вывода

[План до live](research/bounded_recovery_issue498_plan.md), фиксируемые
cases/protocol/source/seal — `docs/research/bounded_recovery_issue498`.
Сравнение — с **историческим** json_refs R11: он уже имел generic loop до4
запросов, новый controller имеет максимум3. Это учитывается в расходах,
не выдаётся за новый рандомизированный контроль или чистый эффект одного поля.
Новый acceptance не используется для разработки.

Тесты проверяют wrong→wrong→stop, wrong→correct→один commit, malformed/batch,
timeout, поздний валидный ответ, generated budget, смену снимка, повтор
успешного запроса и error injection. Mock recovery не является результатом LLM.
Live recovery считается только в реально выполненной trajectory с новым
correct после blocked; namespace/protocol repair отделяется от исправления
семантически неверного свободного отчёта. Этот протокол не принимает
произвольный текст отчёта, его создаёт host.

Релиз/версия/тег остаются на паузе. Допуск по одной schema или mock не выдаётся.
