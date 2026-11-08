# R9: предварительный протокол маркеров (#495)

До новых ответов моделей; результатов пока нет. База R8 — #494.
Research-only scripts; продуктовые API/default не изменяются.

Сравнение четырёх arms на development R8: legacy_guard/framed_guard
(прежний matched engine #485), custom_guard и nonce_guard. 36 synthetic
cases × 4 profiles × 5 seeds × 4 arms = 2880 строк. Все cases/expected
публичны до ответов и не являются новым acceptance. Form JSON остаётся
неподдержанной операцией validator; неуспех не считается защитой.

Custom и nonce используют одинаковые UNTRUSTED_DATA_START/UNTRUSTED_DATA_FINISH.
Custom имеет постоянный идентификатор, nonce — 128-bit secrets token на trial.
Host генерирует descriptor после получения всех payload, проверяет коллизии,
передаёт согласованную инструкцию и envelope. Payload не задаёт настройки.
Decoder проверяет внешний envelope и неизменный внутренний framed.
Это исследование внешней обёртки, а не новая версия транспортного контракта.
Inner framed сохраняет знакомую маркировку; такой дизайн не проверяет
полную замену всех маркеров. Дополнительная инструкция и token overhead
в custom/nonce раскрыты; их сравнение с framed не является чистой
рандомизацией при абсолютно одинаковой длине/инструкции. Custom vs nonce имеют одинаковые имена границ и шаблон инструкции;
различается только значение идентификатора. Это основная парная проверка
гипотезы рандомизации.

Нет обещания, что nonce скрыт от атакующего: он сохраняется в seal/replay.
Известный nonce с точным совпадением в payload отвергается детерминированно
до вызова модели; это транспортный отказ, не устойчивость модели.
Смысловые атаки без совпадения nonce проходят к модели. Исчерпание восьми
регенераций даёт явный отказ. Невалидные настройки не применяются.

Схемы tools/задачи и validator/цикл исполнения переиспользуются из #485
через отдельный динамически загруженный экземпляр модуля; его файлы не
редактируются, глобальные hooks основного модуля не меняются. Сохраняются
все requests/responses/native proposals/events и фактические файлы.
Сгенерированные host-поля proposal не исправляют report модели.

Перед live-run: полный local gate → commit исходников/cases → freeze seal.
Все участвующие Python source files и system/cases/protocol хешируются.
Runtime проверяется LocalOllama по прежним digest/версии. Изменение после
freeze требует новой серии; restart ради цифр запрещён.

Команды: `python -m scripts.marker_experiment_issue495 freeze`, затем
`python -m scripts.marker_experiment_issue495 run --output marker-results`.
PAUSE в output останавливает перед новым trial. `--resume` проверяет
предыдущий ledger/replay/runtime; незавершённый artifact запрещает повтор.
`python -m scripts.marker_experiment_issue495 audit --output marker-results`
воспроизводит запросы, решения и bytes файлов без модели. Исходники остаются
проверяемыми; partial/ошибки сохраняются. Перед каждым рабочим шагом host
проверяет недельную квоту; при <=8% создаёт PAUSE и сохраняет прогресс.

R9 допускает NO-GO: новые теги не становятся default по одному снижению
exact target hit. Обязательны wrong commits, useful correct, no_report,
unsupported, ошибки, limits, токены и latency с разрезом profile/task/kind.
