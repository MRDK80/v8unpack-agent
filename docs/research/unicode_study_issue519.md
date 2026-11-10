# S4: Unicode — замороженный стенд (#519)

Это исследовательский запуск по [S1](unicode_image_protocol_issue516.md),
а не новый production admission. Результаты появятся после публичной
регистрации seal и полного исполнения; до неё генерация не выполняется.
Старые R16 исходники, инструкция, schema, host и scorer не меняются.

## Входы и сравнение

[cases.json](unicode_study_issue519/cases.json) содержит48 synthetic случаев,
manual expected отдельно от запроса модели: по4 clean,10 attack variants,
2 unsupported на каждую из трёх задач. Корпус конструирует
`scripts/unicode_cases_issue519.py`; детерминированный тест сверяет исходник,
manual gold, producer/consumer provenance и фактические файлы.
Невидимые controls не объявляются допустимыми BSL-идентификаторами:
экспортируемые имена — обычные кириллические идентификаторы, а необходимые
Unicode-значения отдельно проверяются в литерале запроса и имени реквизита.
Это синтетический распакованный layout, не доказательство roundtrip через CF.

[plan.json](unicode_study_issue519/plan.json) задаёт1152 траектории Unicode
и160 однократных decoding probes. Порядок профилей/seed фиксирован, arms
вращаются по case/seed. `minimal_guard` соответствует прежнему внутреннему
`candidate_guard`; alias хранится в protocol_row и не добавляется в host ID.
Остальные arms — `legacy_guard` и `framed_guard`. Полномочия и trusted prompt
одинаковы; minimal меняет объём наблюдения, а не только разделители.
Для exports комментарий может вообще не попасть в minimal: это исключение
канала доставки, не обнаружение атаки моделью.

Один ASCII-призыв выбрать `other`/`F9` представлен десятью вариантами.
Это не десять независимых атакующих намерений. Два режима decoding probe —
без **дополнительной** подсказки и с подсказкой кодека; некоторые источники
сами содержат hint, что записывается отдельно. Детерминированный decoder
проверяет конструкцию, но модели инструмент декодирования не получают.
Exact recovery строки не означает исполнения или полного понимания.

## Замораживание и воспроизведение

`scripts/unicode_study_issue519.py initialize` создаёт inputs/runtime,
обращаясь только к metadata Ollama, без генерации. После коммита и полного
local gate `freeze` сохраняет exact source ZIP, code-seal и input seal.
До первого ответа они публикуются с preregistration-комментарием в #519.
Состав всех package/examples/scripts Python-файлов входит в seal, включая
неизменённые R16 host, prompts и независимый scorer.

`run --phase unicode|decode --output <private-directory>` исполняет полный
план. `PAUSE` в output останавливает допуск перед следующей траекторией;
`--resume` сначала проверяет весь сохранённый префикс и runtime. Наличие
незавершённого artifact не допускает автоматического повтора: требуется
отдельное явное решение с сохранением исходной попытки.

До RPC fsync сохраняет запрос, до обработки host — полный parsed response,
включая thinking; ошибки транспорта остаются с техническим кодом и hash
полного private exception. JSONL ledger, RPC checkpoints, исходные файлы,
producer/consumer effects и часы проверяются полным offline replay.
Публичный архив сохраняет все запросы/ответы/эффекты/ошибки и commitments;
private exception text с возможными локальными путями не публикуется.

Ресурсные проверки — observed admission, не OS sandbox: один generation,
проверка свободного диска≥10GiB, артефактов≤2GiB и RSS процесса≤12GiB перед
следующей траекторией. Ollama владеет GPU-памятью отдельно. Deadline180s/RPC
и540s/Unicode trial задаёт прежний driver. Ограничение token/time и неизвестный
usage не исчезают из знаменателя. Новых загрузок весов текстовая фаза не делает.

После `archive` весь архив проверяет
[replay_archive.py](unicode_study_issue519/replay_archive.py): bytes/seals,
полный план, runtime, pinned code, все реальные эффекты и independent scoring.
Полное воспроизведение effects требует Linux/Python3.12/Unicode15.0.0;
проверка целостности и конструкционные тесты не требуют модели или сети.
Latency считается по совпадающим case/seed с действительно запрошенной
legacy-моделью. Пустой comparator не даёт GO. Threshold GO относится только
к этой конечной исследовательской выборке и не отменяет прежний NO-GO.
