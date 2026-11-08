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


## Фактический результат замороженного кандидата

Выполнены все **1440 строк**: **800 реальных модельных траекторий** и640
unsupported без запроса. Полный replay запросов, событий, источников и файлов
PASS; отдельно проверены фактический состав исходных output directories и
отсутствие приватных абсолютных путей в decoded strings. Raw не редактировался.
Source `2d10aff94b23a1c3b62600c966673d63f8c86604`, seal SHA256
`cfdf6a346d798b3724a1eccbda6a4162a8654389c3ca7b3d4c78eb34da9d0405`.

| Arm | Correct / whole scope | Wrong commit | No report | Unsupported без LLM | Limits | Prompt tokens | Generated tokens | Full trial seconds sum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| evidence_guard | 344/720 | 0 | 376 | 320 | 1 | 696060 | 50489 | 834.373 |
| minimal_guard | 349/720 | 0 | 371 | 320 | 0 | 585842 | 46091 | 730.779 |

No report включает unsupported. В запрошенном модельном подмножестве correct
344/400 против349/400; whole-scope остаётся344/720 против349/720.
Clean:139/240 против141/240; attack:205/360 против208/360. Нельзя убрать
неподдержанные формы и объявить полное покрытие. API/response errors0 в обоих;
неверные proposals3/3, native protocol rejections53/46,4steps+no_report49/43.
Наблюдаемого recovery после неверного shape-valid предложения0/0.
Это ошибки предложений/протокола, не доказательство намерения модели.

| Profile / task | Evidence correct /60 | Minimal correct /60 | Minimal clean | Minimal attack |
|---|---:|---:|---:|---:|
| qwen25/exports | 50/60 | 50/60 | 20/20 | 30/30 |
| qwen25/query_text | 50/60 | 50/60 | 20/20 | 30/30 |
| qwen25/form_fields | 0/60 | 0/60 | 0/20 | 0/30 |
| llama31/exports | 0/60 | 4/60 | 2/20 | 2/30 |
| llama31/query_text | 48/60 | 48/60 | 20/20 | 28/30 |
| llama31/form_fields | 0/60 | 0/60 | 0/20 | 0/30 |
| qwen3_off4096/exports | 50/60 | 50/60 | 20/20 | 30/30 |
| qwen3_off4096/query_text | 50/60 | 50/60 | 20/20 | 30/30 |
| qwen3_off4096/form_fields | 0/60 | 0/60 | 0/20 | 0/30 |
| qwen3_on4096/exports | 50/60 | 50/60 | 20/20 | 30/30 |
| qwen3_on4096/query_text | 46/60 | 47/60 | 19/20 | 28/30 |
| qwen3_on4096/form_fields | 0/60 | 0/60 | 0/20 | 0/30 |

У BSL каждые60включают10явно unsupported; поддержанных50. У form_fields все60
unsupported. На Llama exports полезность минимальной проекции4/50, clean2/20,
attack2/30 — **NO-GO**. Это не исправлено компактным JSON.

### Стоимость и решение

В этой серии prompt tokens снизились696060→585842 (около15.8%), generated
50489→46091 (около8.7%). Сумма полных trial durations834.373→730.779секунд.
Полные raw durations сохранены, результаты не аппаратный benchmark.

| Profile/task | Both-correct pairs | Median minimal/evidence full-trial ratio |
|---|---:|---:|
| qwen25/exports | 50 | 0.879 |
| qwen25/query_text | 50 | 0.888 |
| llama31/query_text | 46 | 0.924 |
| qwen3_off4096/exports | 50 | 0.882 |
| qwen3_off4096/query_text | 50 | 0.885 |
| qwen3_on4096/exports | 50 | 0.956 |
| qwen3_on4096/query_text | 44 | 0.784 |

Латентность обеих правильных пар не заменяет whole-scope utility. Это сравнение
с evidence, а не legacy-порог default<=3×R8; новый default/profile допуск здесь
не выдаётся. Старые native NO-GO не переоткрываются по generic matched runner.

**GO ограниченному opt-in API как функциональной проекции**: инварианты полноты,
точности значимых данных и scoped read проверены; стоимость в данном наборе
меньше. **NO-GO автоматической смене default/новому production model admission**
до отдельного клиента/приёмки. Ноль неверных commit обеспечивался тем же
host validator и не стал новой гарантией модели. Прирост5правильных результатов
не доказывает устойчивого общего преимущества на неизвестных атаках.

Оба arms использовали одну новую общую инструкцию о двух схемах. Сравнение
между ними matched, но сравнивать абсолютные числа с R9 как чистый эффект
одного изменения нельзя. Случаи известные development, пять seeds не пять
независимых атак. Host извлёк статические факты заранее; LLM не приписывается
их вычисление. Новый raw-read tool в этой серии не испытывался на модели: API
проверен синтетическими тестами, native binding — отдельная работа.

### Артефакты и воспроизведение

[Все raw записи gzip](minimal_context_issue496/results/records.jsonl.gz),
[summary](minimal_context_issue496/results/summary.json),
[runtime](minimal_context_issue496/results/runtime.json),
[metadata](minimal_context_issue496/results/metadata.json),
[manifest](minimal_context_issue496/results/manifest.json). Только synthetic данные.

В отдельном checkout принятого R10 PR с исходными байтами/autocrlf=false,
Python3.12 и зависимостями проекта, без Ollama/моделей/сети:

```bash
python docs/research/minimal_context_issue496/replay_archive.py
python docs/research/minimal_context_issue496/summarize.py
```

Первый скрипт проверяет manifest/raw/план/запечатанные исходники и реально
воспроизводит каждый tool effect в новом TemporaryDirectory. Второй агрегирует
предзарегистрированные метрики по точным independent expected cases и
сохранённым временам. Последующие изменения кода требуют старого checkout;
исторические ответы не становятся проверкой новой реализации.
