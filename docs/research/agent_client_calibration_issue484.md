# Calibration reference-клиента #484

Полная серия завершена без изменения sealed source/prompts/schemas/cases/policy после первого ответа. Это clean compatibility, не проверка устойчивости к новым атакам и не held-out.

- Source implementation: `7eaa45809c1d6c68a8ced424c3ad38247c0cbbea`; seal SHA-256 `d57064617f3ba540588ac608cf48e7389c46d23cea0c032216fa58c1db64a34e`.
- [Предрегистрация до ответов](https://github.com/MRDK80/v8unpack-agent/issues/484#issuecomment-6707987724).
- [Протокол](agent_client_issue484/protocol.json), [cases](agent_client_issue484/cases.json), [полный plan/seal](agent_client_issue484/seal.json).
- [Raw traces/runtime/summary/manifest](agent_client_issue484/results/manifest.json): 246 строк, 225 model trajectories; исходные JSONL 1 714 763 байта, gzip 129 016 байт. Только синтетические публичные данные; приватные пути в trace отсутствуют.
- Полный offline replay запросов, native events и фактических файлов: PASS. Held-out не запускался.

## Результаты по операциям

Каждая native/reader ячейка содержит 15 случаев (3 clean/benign-lookalike × 5 seeds). Повторные seeds не являются независимыми атаками. Порог native заранее: correct>=95%, ноль no_report/errors/limits/wrong commits. Здесь это требует 15/15.

| Профиль | Native exports | Native query | Reader exports: строгий JSON | Reader query: строгий JSON | Native допуск |
|---|---:|---:|---:|---:|---|
| qwen25 | 10/15 | 15/15 | 0/15 | 9/15 | только query |
| llama31 | unsupported 15/15 | 0/15 | 10/15 | 15/15 | NO-GO |
| qwen3_off4096 | 13/15 | 15/15 | 9/15 | 5/15 | только query |
| qwen3_on4096 | 14/15 | 14/15 | 9/15 | 15/15 | NO-GO |

Детерминированный путь: exports 3/3, query 3/3, всего **6/6 без модели**. Результат не приписывается Qwen/Llama.

Native: 81 правильный commit из 120 строк; 15 строк Llama exports заранее unsupported без вызова модели, ещё 24 завершились без отчёта. Ошибочных фактических commit — **0**. API errors — 0; один token-limit в Qwen3 thinking exports. Это не 100% полезность и не основание считать отказ защитой.

Reader: правильный строгий JSON в 72/120 ответах; JSON вообще декодируется в 90/120. Остальные 30 имеют формат вне объявленного контракта, ещё 18 JSON-ответов не совпадают с эталоном/схемой. Это метрика точного JSON-ответа, не общая оценка способности модели понимать BSL. Reader не пишет **ни в одном** случае; отсутствие записи заложено в его полномочиях, а не доказывает устойчивость ответа.

Поле `no_report` frozen summary вычисляет любой результат, не совпавший с эталоном. Для reader это ошибка точного JSON-ответа, а не число отсутствующих файлов (файлов reader не должен создавать). Для native при нуле неверных commit оно совпадает с отсутствием нового правильного отчёта и включает unsupported. Здесь эти категории разделены явно; исходный scorer и результаты после ответов не редактировались.

## Допуск и ограничения

- **Условный clean GO:** native query у Qwen2.5 и Qwen3 без thinking, 15/15. Это ещё не допуск default/релиза: attack utility и парная latency остаются #485.
- **NO-GO:** все native exports; Llama query 0/15; Qwen3 thinking query 14/15. Они не проходят frozen критерии. По умолчанию native не запускается без host admission; данных модели для его включения недостаточно.
- Llama query в прежнем прототипе #480 имел предварительный GO, но готовый клиент его не подтвердил. Все 15 native query строк получили блокировку multi-call; часто модель отправляла read_source и save_report до получения данных либо затем пыталась писать без успешного чтения. Это несовместимость/потеря полезности текущего интерфейса, не успех защиты. Текстовая имитация tool call не выполнялась.
- У Qwen2.5 и Qwen3 без thinking отмечены report_mismatch на clean случаях, у thinking — в том числе один token-limit. Промпт/интерфейс не менялся и ответы не перезапускались ради результата.
- Reader остаётся только read-only. Точный JSON query у Llama/thinking 15/15 не даёт ему права записи и не делает его summary доверенным.
- Детерминированный путь покрывает ограниченную синтаксическую задачу; полный AST, бизнес-смысл, произвольные патчи и metadata формы этим не доказаны. TOCTOU требует host exclusivity/immutable workspace, как в #483.
- Инструкция/реальный read_source цикл/схемы отличаются от исторического прототипа. Сравнение #480→#484 показывает переносимость клиента, а не чистый эффект одного формата или thinking. Большая серия атак и matched ablation остаются отдельной предрегистрацией #485.

## Повторная проверка

Из клона с dependencies: `python -m scripts.audit_client_results_issue484` — без весов, сети и прежних артефактов. Проверяет pinned seal/source hashes, runtime, полный plan, запросы/ответы, события, файлы и независимые эталоны. Работает в shallow checkout/архиве без старого Git object; это проверка файлов по опубликованному seal, не подпись доверенного автора.

Прямой frozen runner `agent_client_calibration_issue484 audit` дополнительно требует исходную историю source commit. После squash используйте portable audit или сохранённую task-ветку. Последние разряды сумм wall-time допускают только численную погрешность CPython 3.10/3.12 (1e-6 секунды); значения/политика/метрики сравниваются точно. Diagnostics Unicode version проверяется по sealed runtime; исходные данные не нормализуются заново.

Новый цикл улучшения интерфейса, если потребуется, должен быть объявлен отдельно; этот прогон не используется для подгонки текущей защиты. Релиз #475 остаётся на паузе.
