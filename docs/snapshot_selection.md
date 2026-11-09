# Combination/1: bounded snapshot selection (#502)

Новый opt-in reference protocol в
[`snapshot_selection_agent.py`](../examples/snapshot_selection_agent.py).
Это комбинация минимальных фактов, выбора ссылок, bounded recovery, immutable
publication, static form evidence и downstream provenance. Старые frozen
R11/R12/R15 и их NO-GO не исправлены задним числом. CLI/default/root exports
не меняются. До собственной fresh acceptance новый кандидат **не допущен**.

`SelectionProjection(host_contract)` повторно извлекает полный статический
отчёт через независимые BSL/form validators. Host хранит immutable source,
proof hashes/ranges и точные значения. Модель видит только один namespace:
`facts[*].fact_id` = F1,F2,… в пределах текущего запроса, плюс необходимые
name/value. Длинный proof fact_id не дублируется как альтернативный selector.
Никаких aliases старого R11. View/report возвращают копии, source не изменяется.

Модель предлагает ровно `{target_id: "approved", fact_ids: ["F1",…]}`. Это
данные, а не authority. Host требует полный набор без пропусков/дубликатов/
чужих ID и строгие типы, затем материализует собственные исходные значения.
Неверное предложение не заполняется ожидаемым набором. Сами краткие ID не
секреты/capabilities; source/request/output задаются вне модельного ответа.
Ответ конкретного RPC обязан связать с текущим host доверенный клиент.

`SelectionHost` сначала реально публикует независимый R15producer report,
получает host-memory receipt, затем имеет отдельный R15consumer grant. После
полной selection consumer **ещё раз** проверяет source/provenance/target/report
и только тогда создаёт настоящий downstream report. Exact instruction-like
literal сохраняется как данные. Нельзя регистрировать projection/registry/
producer/consumer/commit как инструменты; только строгий dispatcher `execute`.
Python host trusted, не OSsandbox. Public hash/JSON label не authentication.

Эффект — прежняя Linux/private local-FS dirfd/atomic no-clobber граница;
[ограничения R13](snapshot_report_executor.md) и
[R15](report_provenance.md) сохраняются: snapshot-only, без current-file,
malicious Python/same-uid/root/hostileFS/crash/all-external-consumers гарантий.
Другие платформы unsupported. Формы только covered static flat attributes,
BSL только covered grammar; неполнота не пустой полный набор. Max1000facts
не обещает, что модель перечислит1000ID в своём token budget.

Статический отчёт может вычислить host **без LLM**. Модель здесь выполняет
ограниченный выбор/control step, не доказывает бизнес-смысл и не восстанавливает
истину из произвольного текста. Host materialization не называется model
immunity; no_report и unsupported не полезное выполнение. Нельзя переносить
конечный profile/task допуск на другие модели/инструменты/произвольные патчи.

[План R16](research/final_acceptance_issue502_plan.md) задаёт одинаковые task,
схему/budgets для rawlegacy/rawframed/minimalcandidate, fixed safe feedback,
max3RPC, отдельные reader/deterministic controls, новую clean admission доfresh
acceptance, все raw и offline replay. Common READER_SYSTEM прежняя; nonce_probe
добавляет только прежнюю R9boundary instruction с текущими tags. Это отдельный
research renderer, не новый product/default/configurable tags API. Known nonce
collision — refusal/no_report, не устойчивость модели. Noncecommitments public
доэксперимента, unknown draws не раскрываются attacker доего выбора payload.

`python scripts/demo_final_issue502.py` воспроизводит два настоящих исторических
R15ответа и реальные файлы на pinned code без новых запросов. Явно сообщает,
что это recorded replay. `--full-acceptance` дополнительно проверяет завершённый
архив R16; до появления всех archive phases он не объявляется готовым приёмочным
результатом. CI не вызывает модели/сеть.
