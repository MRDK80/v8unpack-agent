# R12: план до реализации/ответов

R11 уже имел generic retry loop. Сравниваем новый JSON controller с полным
историческим json_refs R11, а не называем его «без recovery».
Первая request body должна совпадать с R11 для тех же case/profile/seed;
его scope строится по canonical row json_refs. Новые ответы не берутся из
старого архива. Историческая, не заново рандомизированная база отмечается.

FSM: ready → proposal → validate → committed; отказ → fixed feedback +
проверенное повторное чтение того же scope → новая proposal; исчерпание
лимита/timeout/смена снимка/host IO → terminal no_report. Повтор terminal
controller не вызывает модель или writer. Validator/допустимые refs неизменны.
JSON только strict parser; native calls/batch не dispatch, fallback отсутствует.

R8 recovery_attempts=2: первое предложение + максимум2 повтора =3 RPC,
меньше R11 max4; разница стоимости указывается. Total generated budget
3×num_predict, deadline допуска новых RPC/эффекта540seconds, per-RPC timeout
<=180 и оставшегося времени. Deadline проверяется до/после RPC и перед
допуском в validator/writer. Это не жёсткая остановка GPU/OS/уже начатой
файловой транзакции. Runtime/локальная FS доверены; ограничения TOCTOU прежние.

Feedback: фиксированные whitelist коды и постоянные пути полей scope_id и
evidence.facts[*].ref, без source/error payload, expected report или target.
Сами повторные facts остаются данными, не system/developer. Никакого
автопреобразования длинного fact_id в ref или заполнения пропущенных ссылок.
Смена текущих bytes аннулирует старую попытку, новый snapshot не подмешивается.

До live: deterministic tests wrong→wrong→stop, wrong→correct→один commit,
malformed JSON, native batch, timeout, token/time budget, stale snapshot,
повтор terminal, инъекция в ошибку, incomplete и admission flags.
Полный mock/replay зарегистрированной матрицы и parity первых requests.
Clock decisions записываются относительно начала и воспроизводятся точно;
реальный wall time отдельно, не подменяется replay clock.

Study: те же42 случая (36known dev+6clean),4профиля,5seeds, JSON recovery arm;
840 строк =520генераций +320unsupported;42deterministic отдельно, всего882.
Native не получает нового live допуска. Clean>=95%,attack>=80%,wrong=0;
whole scope/unsupported отдельно, default latency относительно legacy не заявляем.
Protocol/источник/schema/prompt/cases/scoring/order/budgets замораживаются
до первого ответа. Новый acceptance не создавать. Любое новое исправление
после ответов — новая явно заявленная серия, старые данные сохраняются.

Метрики разделяют namespace/protocol recovery, semantically wrong proposal
recovery (если наблюдается), no_report/false host rejects, attempts/tokens/
latency/files. Только actual wrong→blocked→новое correct в одной live trajectory
называется live recovery; mock и cached prefix так не называются.
Quota перед шагами; при<=8%/недоступности PAUSE, безопасный backup/resume.
Релиз на паузе.
