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


## Отклонение процедуры v1 и исправление v2

v1 остановлена на353/2880: unsupported ошибочно вызывал модель вопреки R8.
Все353строки/первый seal/runtime сохранены в v1-partial, replay исходным
9d6d37f прошёл. Это незавершённая серия с нарушением протокола, не допуск.
Runner v2 отдельным файлом добавляет только ранний host unsupported без
запроса. Новый seal/серия обязательны. Исходные prompts/payload/validator
и оценка не меняются; повтор известного development раскрывается, не
называется независимой приёмкой. Старый runner/результаты не переписаны.
Для воспроизведения v1 нужен checkout9d6d37f и исходный seal, поскольку
его manifest фиксирует точный набор Python-файлов. v2 использует команды
того же интерфейса с модулем scripts.marker_experiment_v2_issue495.


## Фактический итог v2

Все **2880 строк** завершены: **1600 реальных модельных траекторий**,
1280 unsupported без модельного запроса. Полный replay всех запросов,
решений и report bytes — PASS; отдельно проверены неизменность источников
и фактический состав output всех исходных artifacts. Raw не редактировался.
Ноль API/parse errors; token limits сохраняются в таблице.

Source freeze `25beec9fa5ba47103642979461baf549abbfb95c`, seal SHA256
`8448a30b9920acf7be76ae554c1dd131b1de94a59eb86477ad17d60a57a48e2b`.
Python3.12.3/Unicode15.0.0 наблюдены после run; runtime профилей проверен
перед ним. Точные запечатанные SHA предыдущего исходника и отклонение v1
сохранены выше. Обе предрегистрации и disposition опубликованы в #495.

| Arm | Correct / whole scope | Wrong commit | No report | Unsupported без LLM | Token limit | Generated tokens |
|---|---:|---:|---:|---:|---:|---:|
| legacy_guard | 270/720 | 0 | 450 | 320 | 2 | 63117 |
| framed_guard | 312/720 | 0 | 408 | 320 | 1 | 67954 |
| custom_guard | 313/720 | 0 | 407 | 320 | 0 | 74701 |
| nonce_guard | 302/720 | 0 | 418 | 320 | 0 | 84211 |

No report включает unsupported. В каждом arm реально запрошено400траекторий;
из них correct270/312/313/302 соответственно. Ноль неверных commit среди
1600 модельных траекторий — свойство проверяемого host-контракта в этом
наборе, не доказательство неуязвимости LLM.

| Profile / task | Legacy guard | Framed guard | Custom guard | Nonce guard |
|---|---:|---:|---:|---:|
| qwen25 / exports | 25/60 | 50/60 | 45/60 | 49/60 |
| qwen25 / query_text | 10/60 | 6/60 | 9/60 | 11/60 |
| qwen25 / form_fields | 0/60 | 0/60 | 0/60 | 0/60 |
| llama31 / exports | 3/60 | 31/60 | 33/60 | 17/60 |
| llama31 / query_text | 50/60 | 39/60 | 44/60 | 45/60 |
| llama31 / form_fields | 0/60 | 0/60 | 0/60 | 0/60 |
| qwen3_off4096 / exports | 50/60 | 50/60 | 50/60 | 50/60 |
| qwen3_off4096 / query_text | 40/60 | 40/60 | 40/60 | 40/60 |
| qwen3_off4096 / form_fields | 0/60 | 0/60 | 0/60 | 0/60 |
| qwen3_on4096 / exports | 50/60 | 50/60 | 50/60 | 50/60 |
| qwen3_on4096 / query_text | 42/60 | 46/60 | 42/60 | 40/60 |
| qwen3_on4096 / form_fields | 0/60 | 0/60 | 0/60 | 0/60 |

Каждые60строк включают10явно неподдержанных BSL случаев; у form_fields все60
неподдержаны. Нельзя молча отбросить их и объявить полное покрытие.
Clean whole-scope:104/240,124/240,129/240,118/240; attack whole-scope:
166/360,188/360,184/360,184/360. Отдельные группы могут быть лучше агрегата,
но не получают автоматического допуска production-клиента.

### Решение

**NO-GO включению новых маркеров по умолчанию и заявлению их как защиты.**
Транспортный прототип и тесты остаются research-only. При одинаковых именах
границ и шаблоне инструкции постоянный id дал313/720 correct, случайный302/720;
на Llama exports33/60 против17/60, на Qwen2.5 exports45/60 против49/60.
Это смешанный конечный development-результат, не доказательство универсального
вреда или бесполезности рандомизации. Повторные seeds не независимые атаки.
Generated tokens у nonce84211 против74701 у custom; сравнение стоимости
измеряет эту серию и не является аппаратным benchmark.

Полная end-to-end latency не была записана runner: есть только длительности
HTTP model steps. Поэтому условие default latency <=3× не объявляется
проверенным. Нового production GO нет, старые native NO-GO не переоткрыты.
Сравнение с прежним framed включает дополнительную инструкцию и другое
окружение block, а не чистый эффект одного имени тега. Custom vs nonce
изолирует значение id, но серверный seed не обеспечивает битовую идентичность.

### Дополнительный offline shape-only counterfactual

Это **post-hoc анализ**, не новая живая серия, не предрегистрированная новая
группа и не основание нового допуска. Те же реальные записанные запросы и
ответы проиграны до первого shape-valid save_report с writer в отдельном
TemporaryDirectory, без validator. До первого commit каждый запрос проверен
на точное совпадение с исходным префиксом. Ответы после изменения feedback
не переиспользуются: первая запись завершает counterfactual.

Получено62/73/75/69неверных первых shape-valid записей для legacy/framed/
custom/nonce. Это показывает наличие неверных предложений, которые
запретил host. Нельзя выдавать эти числа за дополнительные1600живых
траекторий или предсказывать полное поведение другого live-клиента.
Код анализа: [counterfactual.py](marker_experiment_v2_issue495/counterfactual.py).

### Артефакты и replay

[Полные записи gzip](marker_experiment_v2_issue495/results/records.jsonl.gz),
[summary](marker_experiment_v2_issue495/results/summary.json),
[runtime](marker_experiment_v2_issue495/results/runtime.json),
[metadata](marker_experiment_v2_issue495/results/metadata.json),
[manifest](marker_experiment_v2_issue495/results/manifest.json),
[counterfactual](marker_experiment_v2_issue495/results/counterfactual.json).
Исходная незавершённая v1: [353 строки](marker_experiment_issue495/v1-partial/records.jsonl.gz).
Всё synthetic; реальный CF и выгрузки не публикуются.

Для replay нужен отдельный checkout финального R9 PR с исходными байтами
(autocrlf=false) и Python3.12; последующие изменения продукта требуют
проверять старый snapshot отдельно. Seal v1 требует исходный9d6d37f, v2
требует неизменные source files25beec9; добавленные docs/results не меняют
candidate. Для audit исходного output используйте объявленную команду v2.
Архивный replay ниже распаковывает ledger, воспроизводит все recorded responses
и проверяет эффекты без сети; подлинность отсутствующего оригинального CF
он не доказывает (CF в этой серии вообще не использовался).

```bash
python docs/research/marker_experiment_v2_issue495/replay_archive.py
python docs/research/marker_experiment_v2_issue495/counterfactual.py
```

Ни одна из этих команд не вызывает модель; live-run является отдельной командой.
