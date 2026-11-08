# R10: matched полный и компактный evidence (#496)

Протокол до первого нового ответа модели; результатов пока нет. База —
принятый #495, roadmap9e9ede9c53d0aefa1492bf563bf16570348dfa61.

Кандидат: opt-in [task_evidence/1](../task_evidence.md). Старый evidence уже
исключает комментарии. Здесь меняются вложенность и ненужные display metadata,
а необходимые имена/литералы сохраняются точно. Parser/validator не меняются.
Сырой snapshot не передаётся модели автоматически. Scoped read_fact — отдельный
read-only library API с детерминированными тестами, не новый автоматически
зарегистрированный native tool в этой серии.

Матрица: те же36известных development случаев,4профиля,5seeds,2arms:
evidence_guard и minimal_guard. Итого1440строк; ожидаются800реальных модельных
траекторий и640unsupported без запроса. Данные уже известны по R9; это не
новая слепая приёмка. Новый acceptance R16 ещё не создаётся.

Дополнение к R8: для честной пары требуется свежий evidence_guard baseline,
которого не было в исходных7arms. Он добавляет720строк к development cap
(общий максимум этапа development5760 вместо5040). Пороги, payload/expected,
модельные digest, seeds и лимиты не меняются; дополнение фиксируется до ответов.
Машиночитаемый [protocol](minimal_context_issue496/protocol.json).

Оба arms используют одинаковые tools/общую инструкцию; добавлено описание
плоского task_evidence/1 для ОБОИХ до начала серии. Различается observation.
Обе проекции строятся общим prepare; новые и прежние source contracts должны
совпасть. Для каждого выбранного view требуется complete; неподдержанное/
неполное не запрашивает модель и не пишет отчёт. Прежний host validator
независимо проверяет фактический отчёт, никакого auto-correct нет.

Операции: exports/query_text, form_fields остаётся unsupported. Температура0.3,
context8192,4steps,180seconds/request; Qwen2.5/Llama384tokens,
Qwen3off/on4096tokens. Порядок2arms ротируется по case/seed. Все2880строк R9,
включая NO-GO и остановленнуюv1, остаются неизменными.

Новые имена/литералы не фильтруются по подозрительным словам. Бюджеты fact count,
source chars и serialized fact chars дают явную неполноту. Ограниченный read
возвращает только диапазон включённого fact в том же snapshot, без выбора пути.
Полная опись через view.report_contract невозможна при truncated/incomplete;
текущий источник перечитывается и source ID сверяется перед созданием контракта.
Это обязательство потребителя API, не запрет обходных инструментов постороннего host.

## Измерения и freeze

До live-run: полный local gate → source commit → seal всех Python sources,
prompts, cases/expected, protocol и конечного plan → предрегистрация #496.
После первого ответа кандидат/оценка не меняются; ошибки сохраняются, исправление
требует отдельной объявленной серии. Runtime фиксирует Python/Unicode и точные
Ollama/model digest; подмена модели или resume другого runtime запрещены.

Считаются correct/wrong/no_report, clean/attack/unsupported, неверные proposals,
лимиты/ошибки, токены и ПОЛНОЕ время trial (с подготовкой/host-проверками/эффектом).
Отдельно latency обеих правильных matched пар. Нельзя отбросить неполезные случаи
для default GO. Пороги R8 неизменны; старый native NO-GO не переоткрывается.
Статический факт извлекает host; его вычисление не приписывается LLM.

Команды:

```bash
python -m scripts.minimal_context_issue496 freeze
python -m scripts.minimal_context_issue496 run --output minimal-results
python -m scripts.minimal_context_issue496 audit --output minimal-results
```

PAUSE в output завершает текущий bounded trial и останавливает перед следующим.
`--resume` проверяет весь префикс replay и runtime; незавершённый artifact требует
явного disposition, не скрытого повтора. Ledger flush/fsync на каждой строке.
Перед каждым рабочим шагом контролируется недельная квота; <=8%/unknown — безопасная
остановка и сохранение прогресса. Релиз #475 остаётся на паузе.


## Совместимость исторических replay

Предзапусковой полный гейт обнаружил glob старых sealed-наборов, который
считает добавленный opt-in модуль изменением исторического эксперимента.
Старые исходники/seals/results не меняются: [pinned source archive](frozen_history_issue496/README.md)
даёт отдельный временный tree и запускает прежние строгие audits со всеми
исходными проверками. Три archive-теста явно используют этот путь с прежними
assertions. Это воспроизведение истории, не принятие текущего кандидата
старыми ответами; остальные тесты и новая серия проверяют текущий API.
README также сокращён до установленного лимита20000bytes без удаления
ссылки на новый контракт. Новых модельных ответов на момент исправлений нет.
