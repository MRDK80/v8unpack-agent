# Итоговый замер общего поиска — #370

Измерение выполнено владельцем по протоколу #321 через публичный API #328. Приёмка репозиторных артефактов выполняется отдельно: успешное измерение не означает успешный gate/CI или TASK DONE.

## Решение о приёмке #383 (25 октября 2026)

[Решение владельца в #383](https://github.com/MRDK80/v8unpack-agent/issues/383):
правило Б — принятие ограниченного результата по срезам с n ≤ 8.
Это изменение условий приёмки, а не исправление ранжирования и не новый замер.
Исходные наборы, gold, первичный JSON #370 и исторические результаты ниже
сохранены; отметки FAIL в таблицах не заменяются на pass.

Блокируют закрытие #383 и приёмку #329:

- регрессии относительно #370 при тех же наборах, gold, модели и протоколе
  на общем free любого корпуса и на срезах с n > 8;
- exact accuracy@1 ниже 1.0 на восьми сохранённых контролях, включая e4;
- ambiguous без полного набора кандидатов;
- хотя бы один ложный exact на negative.

На непустых срезах с n ≤ 8, как free, так и не-free, недобор
`recall@5 >= 0.80` и `mrr@10 >= 0.60`, а также регрессии относительно #370
фиксируются с числами и знаменателями, но не блокируют приёмку.
Исключения не распространяются на exact, ambiguous, ложные exact на negative
и общий free. Для применимых непустых free-срезов с n > 8 требования порогов
сохраняются. Пустой срез не считается прошедшим проверку.

Четыре отклонения исходного измерения #370 приняты как известные ограничения:

| Корпус / срез | n free | recall@5 | mrr@10 | Ограничение |
|---|---:|---:|---:|---|
| issue321 / tag:paraphrase | 4 | 1.0000 | 0.4875 | mrr@10 ниже 0.60 |
| issue369 / common_module | 8 | 0.7500 | 0.6146 | recall@5 ниже 0.80 |
| issue369 / external_data_processor_form | 8 | 0.8750 | 0.3917 | mrr@10 ниже 0.60 |
| issue369 / external_report_form | 8 | 1.0000 | 0.4875 | mrr@10 ниже 0.60 |

Числа в этой таблице относятся к историческому runtime #370
`7124fda57aa1f1b5fbc34fda56082f711c273754`, не к SHA публикации дополнения.
Размер n — число запросов, не число независимых наблюдений; формулировки
одного intent_id зависимы. Малые срезы ограничивают обобщение результатов,
но сами по себе не доказывают, что недобор является статистическим шумом.

Исторический exact 7/8 ниже также сохраняется как результат #370, а не как
утверждение о текущем диспетчере. Исправление e4 выполнено в
[#385](https://github.com/MRDK80/v8unpack-agent/issues/385);
[отдельный baseline #391](https://github.com/MRDK80/v8unpack-agent/issues/391#issuecomment-6386606198)
относится к runtime `e6524f4b548ac1bd994e976a5102302fabc6f2ba`.
Доказательства этого baseline не смешиваются с измерением производительности #370
и не заменяют gate/CI принимаемого SHA.

Исследовательские решения сохраняются: S-A отклонена в
[#386](https://github.com/MRDK80/v8unpack-agent/issues/386),
[#387](https://github.com/MRDK80/v8unpack-agent/issues/387) закрыта как not_planned;
[#389](https://github.com/MRDK80/v8unpack-agent/issues/389) не обосновала новый
модельный запуск. Отрицательный результат исследования H0 в
[#392](https://github.com/MRDK80/v8unpack-agent/issues/392) не является внедрением.
Правило Б не разрешает эти варианты и не требует новых диагностических задач
по четырём перечисленным срезам для закрытия #383.

Оценка проведена на синтетическом demo-контуре. Выводы о качестве поиска,
включая отказ от S-A (#386/#387), на реальных выгрузках не подтверждены.
Реальные выгрузки в #383 вне scope; где проверять перенос на реальные данные,
решается отдельно. BGE-M3 не объявляется production default.

Закрытие #383 требует подтверждения обязательных проверок на принимаемом SHA,
записи ограничений в docs и CHANGELOG и доказательного комментария.
Это дополнение само по себе не закрывает #383/#329/#320, не разрешает merge
или релиз и не подменяет сквозные проверки форм в #384.

## Результаты и ограничения

BGE-M3 проходит оба корпусных порога free, но не все срезы. Эталоны и пороги после измерений не менялись; общий критерий качества #329 не отмечается автоматически. Production default не выбран.

| Корпус | Метод | n free | hit@1 / recall@5 / mrr@10 / ndcg@10 | Пороги |
|---|---|---:|---|---|
| issue321 | lexical_baseline | 17 | 0.6471 / 0.7647 / 0.6814 / 0.7018 | FAIL |
| issue321 | bge_m3 | 17 | 0.7647 / 1.0000 / 0.8402 / 0.8793 | pass |
| issue369 | lexical_baseline | 152 | 0.1447 / 0.3816 / 0.2439 / 0.3116 | FAIL |
| issue369 | bge_m3 | 152 | 0.6908 / 0.9605 / 0.7946 / 0.8418 | pass |

Ниже планки BGE-M3: issue321/tag:paraphrase (n=4, mrr@10=0.4875); issue369/common_module (n=8, recall@5=0.7500), формы внешних обработок (n=8, mrr@10=0.3917), формы внешних отчётов (n=8, mrr@10=0.4875). В новом наборе 16/19 основных групп проходят оба порога. Общая сводка forms скрывает различия внешних форм, поэтому она не заменяет разбивку.

Exact: 7/8 = 0.875 при цели 1.0; e4 «Загрузка Цен» остаётся в знаменателе и фактически возвращает empty в режиме exact из-за старого эталона по синониму. Индивидуальная auto-выдача e4 не сохранена; её ранг и similarity не заявляются. Ambiguous: полные множества совпали 4/4 и 8/8. Ложных exact на negative: 0/7 и 0/8. В новом корпусе нет положительных exact-запросов (n=0).

Синтетические процедуры задают действие комментариями, а не развёрнутой прикладной реализацией. Сканеры, collect_search_corpus и CLI данным запуском не проверялись. CommonForm и внешние формы остаются unconfirmed: 6 связей нового корпуса неподтверждённые, 28 подтверждённые; в старом корпусе 21 подтверждённая связь. Причина провалов внешних форм не установлена; абляции полей владельца не проводились. #371/#372 не выполнены этим измерением.

## Протокол и воспроизведение

[Протокол до измерений](https://github.com/MRDK80/v8unpack-agent/issues/370#issuecomment-6234319876). Runtime source SHA: `7124fda57aa1f1b5fbc34fda56082f711c273754`, source/base `epic/320-unified-search`. Репозиторный runner намеренно извлекает этот зафиксированный runtime, а не произвольную установленную версию пакета.

Корпуса независимы: issue321 — прежние 21 артефакт, 90 фрагментов, 42 запроса; issue369 — собственные 34 артефакта, 140 фрагментов, 168 запросов. Четыре configuration:-:Configuration:<kind> сопоставлены с configuration:::<kind> без изменения исходных файлов. Межкорпусные идентичности не объединяются.

Путь: данные → штатные ModuleEntry/ArtifactRef/OwnerRef/OwnerLink/SearchDocument/SearchDocumentSet/SearchCorpus → fragment_corpus(max_chars=2000, overlap_lines=3) → search_api.build_search_snapshot → load_index + assess → search_api.search_with_context → context.dispatch.result.hits. Контекст: max_chars=-1, max_tokens=None; поиск: auto, top_k=10, filters=None. Поисковый текст не содержит gold, rationale, intent_id, оценочные группы или аннотации.

Основные метрики artifact-level: query-macro hit@1, recall@5, mrr@10, ndcg@10; max по фрагментам и дедупликация артефактов до top-k. Пороги free: recall@5 >= 0.80, mrr@10 >= 0.60. Mixed и free+mixed отдельные; ошибки не должны исчезать из знаменателя. Fragment gold служит отдельной диагностикой; координатные окна не переименовываются под эталон.

Baseline: reconstructed_bm25_on_runtime_fragments, k1=1.5, b=0.75, Unicode-слова >3 символов, пятисимвольные основы после split_names. Тот же fragment.search_text и запросы, без дополнительной обрезки. Baseline tie-break: меньший document_id лучшего фрагмента; порядок модельных hits — из публичного API. Это не побайтовое повторение исторического runner #321/#357; whole заново не перебирался.

Модель BAAI/bge-m3 @ `5617a9f61b028005a4858fdac845db406aefb181`: dense-only SentenceTransformer, CLS, dimension=1024, normalize_embeddings=True, пустые query/document prefixes, max_seq_length=8192, CPU/float32, batch_size=4, threads=4, inter_op_threads=1, seed=0. Офлайн, local_files_only=True, trust_remote_code=False; сокеты блокируются. Model load измеряется отдельно. Размер весов pytorch_model.bin — 2271145830 байт, SHA-256 `b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`.

Исполненная версия: measure_370_v2.py, 49136 байт, SHA-256 `fd5de5e102de633e97c22e88015dfe039186428ee383c3a86a11c9168224329c`. Исходный результат сохранён побайтно в search_quality_issue370_results.json: 2269127 байт, SHA-256 `e8dcdbb07df887e7374fa8f9bf6cd778bf20532b2b2753690aeae3f7e46cfe02`, status=evaluation_complete, RC=0. Реальный smoke владельца: measure_370_v1.json, SHA-256 `01cabd8ac5338d0039193878878ceba921f70d98522dea5921109868cdbc426a`, RC=0.

Команды репозиторного runner (выходные файлы не должны существовать):

```bash
python scripts/measure_search_issue370.py --repo . --out smoke370.json --smoke-only
python scripts/measure_search_issue370.py --repo . --out baseline370.json --baseline-only
python scripts/measure_search_issue370.py --repo . --out model370.json --run-models --protocol 6234319876
```

Последняя команда требует локальных весов и отдельного согласования дорогого запуска. Перенос runner сохраняет формулы, корпусный адаптер и модельные параметры; добавляет журнал всех попыток, учёт незапущенных запросов и ошибочных ветвей. Новые измерения этой версии не подменяют сохранённый исходный прогон. Gate не запускает модель.

| Вход | Байт | SHA-256 до и после |
|---|---:|---|
| search_eval_issue321.json | 29006 | `35166f3e74ca7047731fc05a1cf3ed564530a02cf65cf03f2371d3ff48efa052` |
| search_eval_issue321_hard.json | 33429 | `f94c3d222eb34d2c9b629a3c51379fc7661f42dd252388b1b596da5026b70ec3` |
| search_eval_issue329_paraphrase.json | 255707 | `5b69e413527a7fc733c525c997a8031310a538fcaa3f584a32b3b31881eee46b` |

Digest artifacts нового набора: `5e5d945a46bf79b9009d16173cae344f5fd59c4cc61272e3bc4f43e2573ee7d7`; json.dumps с ensure_ascii=False, sort_keys=True, separators=(comma, colon), allow_nan=False, затем LF/UTF-8. Representation version: `c8eb4789dfe3b2db716b5198180bfb9b4e2f3bea71aaf933eaf6ec7edb944c9f`.

issue321: corpus_projection_sha256 `6684238a345a206edda57d5d620cb5a3091370ab7e3920fce846b19d00bb0e28`, search_inputs_sha256 `283d55314b49b174dbcf03b91337ccac2b44e3e7ea136a3773d3450656b38a60`.

issue369: corpus_projection_sha256 `b12fed7430cb255c518a20b7a777160876c6f35bf6c466a1a2aff4aa4b5bb956`, search_inputs_sha256 `6b46bd77ad3b167aeb68a226c82a36ddbc2adedf3245750bf35a528677ead392`.

## Срезы и намерения

Ячейки: hit@1 / recall@5 / mrr@10 / ndcg@10. Таблицы показывают free-метрики и n free. Пересекающиеся срезы не суммируются, n=0 — not measured. Полные сводки free+mixed сохранены в JSON.

### issue321

| Срез | n free | BM25 | BGE-M3 | Пороги BM25 / BGE |
|---|---:|---|---|---|
| `class:free` | 17 | 0.6471 / 0.7647 / 0.6814 / 0.7018 | 0.7647 / 1.0000 / 0.8402 / 0.8793 | FAIL / pass |
| `class:free+mixed` | 17 | 0.6471 / 0.7647 / 0.6814 / 0.7018 | 0.7647 / 1.0000 / 0.8402 / 0.8793 | FAIL / pass |
| `class:mixed` | 0 | not measured | not measured | not measured / not measured |
| `forms` | 2 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `large` | 4 | 0.5000 / 1.0000 / 0.6458 / 0.7327 | 0.7500 / 1.0000 / 0.8333 / 0.8750 | pass / pass |
| `module_kind:command` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `module_kind:common_module` | 2 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 0.5000 / 1.0000 / 0.6000 / 0.6934 | pass / pass |
| `module_kind:external_connection` | 0 | not measured | not measured | not measured / not measured |
| `module_kind:form` | 2 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `module_kind:managed_application` | 0 | not measured | not measured | not measured / not measured |
| `module_kind:manager` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `module_kind:object` | 6 | 0.5000 / 0.6667 / 0.5417 / 0.5718 | 0.6667 / 1.0000 / 0.8056 / 0.8552 | FAIL / pass |
| `module_kind:ordinary_application` | 0 | not measured | not measured | not measured / not measured |
| `module_kind:record_set` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `module_kind:service` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 0.5000 / 1.0000 / 0.6250 / 0.7153 | FAIL / pass |
| `module_kind:session` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `module_kind:value_manager` | 0 | not measured | not measured | not measured / not measured |
| `owner_kind:common_command` | 0 | not measured | not measured | not measured / not measured |
| `owner_kind:common_form` | 0 | not measured | not measured | not measured / not measured |
| `owner_kind:common_module` | 2 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 0.5000 / 1.0000 / 0.6000 / 0.6934 | pass / pass |
| `owner_kind:configuration` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `owner_kind:external_data_processor` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `owner_kind:external_report` | 0 | not measured | not measured | not measured / not measured |
| `owner_kind:metadata_object` | 10 | 0.5000 / 0.6000 / 0.5250 / 0.5431 | 0.7000 / 1.0000 / 0.8083 / 0.8562 | FAIL / pass |
| `owner_kind:metadata_object_command` | 1 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `owner_kind:metadata_object_form` | 2 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `tag:base` | 9 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `tag:distractor` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `tag:paraphrase` | 4 | 0.2500 / 0.2500 / 0.2500 / 0.2500 | 0.2500 / 1.0000 / 0.4875 / 0.6121 | FAIL / FAIL |
| `tag:tail` | 2 | 0.0000 / 1.0000 / 0.2917 / 0.4653 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |

### issue369

| Срез | n free | BM25 | BGE-M3 | Пороги BM25 / BGE |
|---|---:|---|---|---|
| `annotation:distractor` | 14 | 0.1429 / 0.5714 / 0.2857 / 0.3566 | 0.7143 / 1.0000 / 0.8357 / 0.8771 | FAIL / pass |
| `annotation:lexical_disjoint` | 132 | 0.1212 / 0.3182 / 0.2046 / 0.2678 | 0.6818 / 0.9545 / 0.7856 / 0.8344 | FAIL / pass |
| `annotation:tail` | 8 | 0.2500 / 0.3750 / 0.2958 / 0.3429 | 0.5000 / 0.8750 / 0.6667 / 0.7202 | FAIL / pass |
| `class:free` | 152 | 0.1447 / 0.3816 / 0.2439 / 0.3116 | 0.6908 / 0.9605 / 0.7946 / 0.8418 | FAIL / pass |
| `class:free+mixed` | 152 | 0.1447 / 0.3816 / 0.2439 / 0.3116 | 0.6908 / 0.9605 / 0.7946 / 0.8418 | FAIL / pass |
| `forms` | 32 | 0.0312 / 0.2812 / 0.1236 / 0.1896 | 0.4375 / 0.9375 / 0.6175 / 0.7101 | FAIL / pass |
| `group:common_command:command` | 8 | 0.1250 / 0.8750 / 0.4062 / 0.5241 | 0.8750 / 0.8750 / 0.8958 / 0.9195 | FAIL / pass |
| `group:common_module:common_module` | 8 | 0.0000 / 0.3750 / 0.1793 / 0.3403 | 0.5000 / 0.7500 / 0.6146 / 0.6772 | FAIL / FAIL |
| `group:configuration:external_connection` | 8 | 0.3750 / 0.5000 / 0.4887 / 0.5762 | 0.5000 / 1.0000 / 0.6542 / 0.7381 | FAIL / pass |
| `group:configuration:managed_application` | 8 | 0.1250 / 0.2500 / 0.2253 / 0.3428 | 0.7500 / 1.0000 / 0.8542 / 0.8914 | FAIL / pass |
| `group:configuration:ordinary_application` | 8 | 0.2500 / 0.3750 / 0.3567 / 0.4443 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `group:configuration:session` | 8 | 0.1250 / 0.3750 / 0.2271 / 0.3440 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `group:external_data_processor:object` | 8 | 0.1250 / 0.2500 / 0.2031 / 0.2715 | 0.7500 / 1.0000 / 0.8750 / 0.9077 | FAIL / pass |
| `group:external_report:object` | 8 | 0.1250 / 0.1250 / 0.1250 / 0.1250 | 0.8750 / 1.0000 / 0.9375 / 0.9539 | FAIL / pass |
| `group:form:common_form` | 8 | 0.1250 / 0.3750 / 0.2188 / 0.2577 | 0.7500 / 0.8750 / 0.7991 / 0.8455 | FAIL / pass |
| `group:form:external_data_processor_form` | 8 | 0.0000 / 0.3750 / 0.1031 / 0.1955 | 0.1250 / 0.8750 / 0.3917 / 0.5374 | FAIL / FAIL |
| `group:form:external_report_form` | 8 | 0.0000 / 0.2500 / 0.0938 / 0.1327 | 0.2500 / 1.0000 / 0.4875 / 0.6121 | FAIL / FAIL |
| `group:form:metadata_object_form` | 8 | 0.0000 / 0.1250 / 0.0786 / 0.1726 | 0.6250 / 1.0000 / 0.7917 / 0.8452 | FAIL / pass |
| `group:metadata_object:HTTPService:service` | 8 | 0.0000 / 0.3750 / 0.1667 / 0.2202 | 0.7500 / 1.0000 / 0.8542 / 0.8914 | FAIL / pass |
| `group:metadata_object:WebService:service` | 8 | 0.1250 / 0.3750 / 0.2125 / 0.2522 | 0.5000 / 0.8750 / 0.6042 / 0.6702 | FAIL / pass |
| `group:metadata_object:manager` | 8 | 0.2500 / 0.2500 / 0.2500 / 0.2500 | 0.7500 / 1.0000 / 0.8750 / 0.9077 | FAIL / pass |
| `group:metadata_object:object` | 8 | 0.0000 / 0.3750 / 0.1083 / 0.1734 | 0.7500 / 1.0000 / 0.8542 / 0.8914 | FAIL / pass |
| `group:metadata_object:record_set` | 8 | 0.3750 / 0.5000 / 0.4062 / 0.4288 | 0.6250 / 1.0000 / 0.7750 / 0.8311 | FAIL / pass |
| `group:metadata_object:value_manager` | 8 | 0.3750 / 0.5000 / 0.4000 / 0.4234 | 0.8750 / 1.0000 / 0.9167 / 0.9375 | FAIL / pass |
| `group:metadata_object_command:command` | 8 | 0.2500 / 0.6250 / 0.3854 / 0.4452 | 0.8750 / 1.0000 / 0.9167 / 0.9375 | FAIL / pass |
| `large` | 16 | 0.1875 / 0.4375 / 0.2755 / 0.3431 | 0.5000 / 0.8750 / 0.6687 / 0.7354 | FAIL / pass |
| `module_kind:command` | 16 | 0.1875 / 0.7500 / 0.3958 / 0.4846 | 0.8750 / 0.9375 / 0.9062 / 0.9285 | FAIL / pass |
| `module_kind:common_module` | 8 | 0.0000 / 0.3750 / 0.1793 / 0.3403 | 0.5000 / 0.7500 / 0.6146 / 0.6772 | FAIL / FAIL |
| `module_kind:external_connection` | 8 | 0.3750 / 0.5000 / 0.4887 / 0.5762 | 0.5000 / 1.0000 / 0.6542 / 0.7381 | FAIL / pass |
| `module_kind:form` | 32 | 0.0312 / 0.2812 / 0.1236 / 0.1896 | 0.4375 / 0.9375 / 0.6175 / 0.7101 | FAIL / pass |
| `module_kind:managed_application` | 8 | 0.1250 / 0.2500 / 0.2253 / 0.3428 | 0.7500 / 1.0000 / 0.8542 / 0.8914 | FAIL / pass |
| `module_kind:manager` | 8 | 0.2500 / 0.2500 / 0.2500 / 0.2500 | 0.7500 / 1.0000 / 0.8750 / 0.9077 | FAIL / pass |
| `module_kind:object` | 24 | 0.0833 / 0.2500 / 0.1455 / 0.1899 | 0.7917 / 1.0000 / 0.8889 / 0.9177 | FAIL / pass |
| `module_kind:ordinary_application` | 8 | 0.2500 / 0.3750 / 0.3567 / 0.4443 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `module_kind:record_set` | 8 | 0.3750 / 0.5000 / 0.4062 / 0.4288 | 0.6250 / 1.0000 / 0.7750 / 0.8311 | FAIL / pass |
| `module_kind:service` | 16 | 0.0625 / 0.3750 / 0.1896 / 0.2362 | 0.6250 / 0.9375 / 0.7292 / 0.7808 | FAIL / pass |
| `module_kind:session` | 8 | 0.1250 / 0.3750 / 0.2271 / 0.3440 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `module_kind:value_manager` | 8 | 0.3750 / 0.5000 / 0.4000 / 0.4234 | 0.8750 / 1.0000 / 0.9167 / 0.9375 | FAIL / pass |
| `owner_kind:common_command` | 8 | 0.1250 / 0.8750 / 0.4062 / 0.5241 | 0.8750 / 0.8750 / 0.8958 / 0.9195 | FAIL / pass |
| `owner_kind:common_form` | 8 | 0.1250 / 0.3750 / 0.2188 / 0.2577 | 0.7500 / 0.8750 / 0.7991 / 0.8455 | FAIL / pass |
| `owner_kind:common_module` | 8 | 0.0000 / 0.3750 / 0.1793 / 0.3403 | 0.5000 / 0.7500 / 0.6146 / 0.6772 | FAIL / FAIL |
| `owner_kind:configuration` | 32 | 0.2188 / 0.3750 / 0.3245 / 0.4268 | 0.8125 / 1.0000 / 0.8771 / 0.9074 | FAIL / pass |
| `owner_kind:external_data_processor` | 16 | 0.0625 / 0.3125 / 0.1531 / 0.2335 | 0.4375 / 0.9375 / 0.6333 / 0.7226 | FAIL / pass |
| `owner_kind:external_report` | 16 | 0.0625 / 0.1875 / 0.1094 / 0.1289 | 0.5625 / 1.0000 / 0.7125 / 0.7830 | FAIL / pass |
| `owner_kind:metadata_object` | 48 | 0.1875 / 0.3958 / 0.2573 / 0.2913 | 0.7083 / 0.9792 / 0.8132 / 0.8549 | FAIL / pass |
| `owner_kind:metadata_object_command` | 8 | 0.2500 / 0.6250 / 0.3854 / 0.4452 | 0.8750 / 1.0000 / 0.9167 / 0.9375 | FAIL / pass |
| `owner_kind:metadata_object_form` | 8 | 0.0000 / 0.1250 / 0.0786 / 0.1726 | 0.6250 / 1.0000 / 0.7917 / 0.8452 | FAIL / pass |
| `tag:paraphrase` | 152 | 0.1447 / 0.3816 / 0.2439 / 0.3116 | 0.6908 / 0.9605 / 0.7946 / 0.8418 | FAIL / pass |

### Mixed и intent-macro

| Класс issue321 | n | BM25 | BGE-M3 |
|---|---:|---|---|
| class:mixed | 6 | 0.6667 / 1.0000 / 0.8333 / 0.8770 | 0.8333 / 1.0000 / 0.9167 / 0.9385 |
| class:free+mixed | 23 | 0.6522 / 0.8261 / 0.7210 / 0.7475 | 0.7826 / 1.0000 / 0.8601 / 0.8948 |

Новый корпус: 19 групп по 8 запросов и 4 intent_id. Всего 76 намерений, по 2 связанные формулировки; пары не независимы. Минимумы — инженерное покрытие, не статистическая гарантия. Intent-macro совпадает с query-macro из-за одинакового размера пар. Диагностическое применение порогов к intent-mean: BGE-M3 61/76, BM25 4/76; это не замена основных метрик.

| Intent | Группа | n | BM25 | BGE-M3 | Диагностические пороги BM25 / BGE |
|---|---|---:|---|---|---|
| `p369-g01-a1-i1` | `configuration:ordinary_application` | 2 | 0.0000 / 0.5000 / 0.3000 / 0.4600 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g01-a1-i2` | `configuration:ordinary_application` | 2 | 0.0000 / 0.0000 / 0.0556 / 0.1505 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g01-a1-i3` | `configuration:ordinary_application` | 2 | 0.5000 / 0.5000 / 0.5714 / 0.6667 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g01-a1-i4` | `configuration:ordinary_application` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g02-a1-i1` | `configuration:managed_application` | 2 | 0.0000 / 0.5000 / 0.1964 / 0.3820 | 0.0000 / 1.0000 / 0.4167 / 0.5655 | FAIL / FAIL |
| `p369-g02-a1-i2` | `configuration:managed_application` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g02-a1-i3` | `configuration:managed_application` | 2 | 0.0000 / 0.0000 / 0.1333 / 0.3226 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g02-a1-i4` | `configuration:managed_application` | 2 | 0.0000 / 0.0000 / 0.0714 / 0.1667 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g03-a1-i1` | `configuration:external_connection` | 2 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | pass / pass |
| `p369-g03-a1-i2` | `configuration:external_connection` | 2 | 0.0000 / 0.0000 / 0.1214 / 0.3112 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g03-a1-i3` | `configuration:external_connection` | 2 | 0.0000 / 0.5000 / 0.3333 / 0.4936 | 0.5000 / 1.0000 / 0.6000 / 0.6934 | FAIL / pass |
| `p369-g03-a1-i4` | `configuration:external_connection` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 0.5000 / 1.0000 / 0.6000 / 0.6934 | FAIL / pass |
| `p369-g04-a1-i1` | `configuration:session` | 2 | 0.0000 / 0.5000 / 0.1000 / 0.1934 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g04-a1-i2` | `configuration:session` | 2 | 0.0000 / 0.0000 / 0.0500 / 0.1445 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g04-a1-i3` | `configuration:session` | 2 | 0.0000 / 0.0000 / 0.1333 / 0.3226 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g04-a1-i4` | `configuration:session` | 2 | 0.5000 / 1.0000 / 0.6250 / 0.7153 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `p369-g05-a1-i1` | `metadata_object:object` | 2 | 0.0000 / 0.5000 / 0.1667 / 0.2500 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g05-a1-i2` | `metadata_object:object` | 2 | 0.0000 / 0.5000 / 0.1000 / 0.1934 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g05-a2-i1` | `metadata_object:object` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g05-a2-i2` | `metadata_object:object` | 2 | 0.0000 / 0.5000 / 0.1667 / 0.2500 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g06-a1-i1` | `external_data_processor:object` | 2 | 0.0000 / 0.5000 / 0.1667 / 0.2500 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g06-a1-i2` | `external_data_processor:object` | 2 | 0.0000 / 0.0000 / 0.0833 / 0.1781 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g06-a2-i1` | `external_data_processor:object` | 2 | 0.0000 / 0.0000 / 0.0625 / 0.1577 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g06-a2-i2` | `external_data_processor:object` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g07-a1-i1` | `external_report:object` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g07-a1-i2` | `external_report:object` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g07-a2-i1` | `external_report:object` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g07-a2-i2` | `external_report:object` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g08-a1-i1` | `metadata_object:manager` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g08-a1-i2` | `metadata_object:manager` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g08-a2-i1` | `metadata_object:manager` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g08-a2-i2` | `metadata_object:manager` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g09-a1-i1` | `metadata_object:record_set` | 2 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | 0.0000 / 1.0000 / 0.3500 / 0.5089 | FAIL / FAIL |
| `p369-g09-a1-i2` | `metadata_object:record_set` | 2 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | pass / pass |
| `p369-g09-a2-i1` | `metadata_object:record_set` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g09-a2-i2` | `metadata_object:record_set` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g10-a1-i1` | `common_command:command` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g10-a1-i2` | `common_command:command` | 2 | 0.0000 / 1.0000 / 0.2917 / 0.4653 | 0.5000 / 0.5000 / 0.5833 / 0.6781 | FAIL / FAIL |
| `p369-g10-a2-i1` | `common_command:command` | 2 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | pass / pass |
| `p369-g10-a2-i2` | `common_command:command` | 2 | 0.0000 / 1.0000 / 0.3333 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g11-a1-i1` | `metadata_object_command:command` | 2 | 0.0000 / 1.0000 / 0.2917 / 0.4653 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g11-a1-i2` | `metadata_object_command:command` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g11-a2-i1` | `metadata_object_command:command` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g11-a2-i2` | `metadata_object_command:command` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g12-a1-i1` | `metadata_object:value_manager` | 2 | 0.0000 / 0.5000 / 0.1000 / 0.1934 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g12-a1-i2` | `metadata_object:value_manager` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g12-a2-i1` | `metadata_object:value_manager` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g12-a2-i2` | `metadata_object:value_manager` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g13-a1-i1` | `metadata_object:HTTPService:service` | 2 | 0.0000 / 0.5000 / 0.1667 / 0.2500 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g13-a1-i2` | `metadata_object:HTTPService:service` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g13-a2-i1` | `metadata_object:HTTPService:service` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g13-a2-i2` | `metadata_object:HTTPService:service` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 0.5000 / 1.0000 / 0.7500 / 0.8155 | FAIL / pass |
| `p369-g14-a1-i1` | `metadata_object:WebService:service` | 2 | 0.0000 / 1.0000 / 0.3500 / 0.5089 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g14-a1-i2` | `metadata_object:WebService:service` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g14-a2-i1` | `metadata_object:WebService:service` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | FAIL / FAIL |
| `p369-g14-a2-i2` | `metadata_object:WebService:service` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.0000 / 1.0000 / 0.2917 / 0.4653 | FAIL / FAIL |
| `p369-g15-a1-i1` | `common_module:common_module` | 2 | 0.0000 / 0.5000 / 0.2292 / 0.4077 | 0.5000 / 0.5000 / 0.5833 / 0.6781 | FAIL / FAIL |
| `p369-g15-a1-i2` | `common_module:common_module` | 2 | 0.0000 / 0.0000 / 0.0833 / 0.1781 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | FAIL / FAIL |
| `p369-g15-a2-i1` | `common_module:common_module` | 2 | 0.0000 / 0.5000 / 0.1964 / 0.3820 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g15-a2-i2` | `common_module:common_module` | 2 | 0.0000 / 0.5000 / 0.2083 / 0.3934 | 0.0000 / 1.0000 / 0.3750 / 0.5308 | FAIL / FAIL |
| `p369-g16-a1-i1` | `form:metadata_object_form` | 2 | 0.0000 / 0.0000 / 0.0625 / 0.1577 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g16-a1-i2` | `form:metadata_object_form` | 2 | 0.0000 / 0.0000 / 0.0556 / 0.1505 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g16-a2-i1` | `form:metadata_object_form` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g16-a2-i2` | `form:metadata_object_form` | 2 | 0.0000 / 0.5000 / 0.1964 / 0.3820 | 0.0000 / 1.0000 / 0.5000 / 0.6309 | FAIL / FAIL |
| `p369-g17-a1-i1` | `form:common_form` | 2 | 0.5000 / 0.5000 / 0.5000 / 0.5000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g17-a1-i2` | `form:common_form` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g17-a2-i1` | `form:common_form` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 1.0000 / 1.0000 / 1.0000 / 1.0000 | FAIL / pass |
| `p369-g17-a2-i2` | `form:common_form` | 2 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | 0.0000 / 0.5000 / 0.1964 / 0.3820 | FAIL / FAIL |
| `p369-g18-a1-i1` | `form:external_data_processor_form` | 2 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | 0.0000 / 1.0000 / 0.2250 / 0.4088 | FAIL / FAIL |
| `p369-g18-a1-i2` | `form:external_data_processor_form` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.0000 / 0.5000 / 0.3000 / 0.4600 | FAIL / FAIL |
| `p369-g18-a2-i1` | `form:external_data_processor_form` | 2 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | 0.0000 / 1.0000 / 0.3750 / 0.5308 | FAIL / FAIL |
| `p369-g18-a2-i2` | `form:external_data_processor_form` | 2 | 0.0000 / 0.5000 / 0.1625 / 0.3512 | 0.5000 / 1.0000 / 0.6667 / 0.7500 | FAIL / pass |
| `p369-g19-a1-i1` | `form:external_report_form` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.6000 / 0.6934 | FAIL / pass |
| `p369-g19-a1-i2` | `form:external_report_form` | 2 | 0.0000 / 0.0000 / 0.0000 / 0.0000 | 0.5000 / 1.0000 / 0.6250 / 0.7153 | FAIL / pass |
| `p369-g19-a2-i1` | `form:external_report_form` | 2 | 0.0000 / 0.5000 / 0.2500 / 0.3155 | 0.0000 / 1.0000 / 0.3750 / 0.5308 | FAIL / FAIL |
| `p369-g19-a2-i2` | `form:external_report_form` | 2 | 0.0000 / 0.5000 / 0.1250 / 0.2153 | 0.0000 / 1.0000 / 0.3500 / 0.5089 | FAIL / FAIL |

Fragment diagnostics: issue321 — top-1 gold-fragment BGE-M3 18/23, BM25 15/23; выбранный фрагмент целевого артефакта 23/23 и 18/23. Issue369 — top-1 99/152 и 5/152; выбранный фрагмент целевого артефакта 139/152 и 25/152. Знаменатели включают запросы, где целевой артефакт не найден в top-10. Неразрешимых fragment gold нет.

## Производительность и negative

Build+save включает encode и запись снимка после подготовки корпуса; load+assess включает чтение, валидацию и проверку свежести. Query — весь search_with_context в режиме auto, включая точные попадания без encode. Это не чистая стоимость semantic encode. Сериализация результата вне query timing. Три повтора после warm-up; build/load n=3 — малая выборка, p95 nearest-rank равен максимуму.

| Корпус | Фрагменты | Build+save median, s | Load+assess median, ms | Query median / p95, ms | n query | Snapshot, байт |
|---|---:|---:|---:|---:|---:|---:|
| issue321 | 90 | 17.038668 | 76.6563 | 86.9626 / 104.6892 | 126 | 2040000 |
| issue369 | 140 | 25.098333 | 119.2126 | 101.5679 / 115.2464 | 504 | 3164473 |

Model load 1.721139 s; весь прогон 201.441816 s; наблюдаемый peak RSS 2609147904 байт. CPU AMD Ryzen 5 5600 6-Core Processor, 12 логических потоков; RAM 33571254272 байт; Linux 7.0.0-38-generic x86_64; Python 3.12.3. Пакеты: huggingface-hub 1.33.0, numpy 2.5.3, pytest 9.1.1, sentence-transformers 6.1.0, torch 2.14.0, transformers 5.17.0, v8unpack 1.2.13.

Ноль токенных усечений документов и запросов. Оба снимка compatible/fresh. Время BM25 ниже не включает публичный контекст, поэтому не является эквивалентным end-to-end сравнением; snapshot/load baseline не измерялись (in-memory).

| Корпус | BM25 build median, ms | Scoring median, ms | Scoring+ranking median / p95, ms |
|---|---:|---:|---:|
| issue321 | 7.0083 | 0.0703 | 0.5648 / 0.6187 |
| issue369 | 9.9561 | 0.1508 | 0.4734 / 0.5301 |

| Корпус / метод | n negative | Top-1 min / median / max |
|---|---:|---|
| issue321 / lexical_baseline | 7 | 0.000000 / 0.627430 / 7.084092 |
| issue321 / bge_m3 | 7 | 0.472047 / 0.535951 / 0.563373 |
| issue369 / lexical_baseline | 8 | 0.000000 / 6.756398 / 8.035449 |
| issue369 / bge_m3 | 8 | 0.444030 / 0.474005 / 0.548870 |

Шкалы cosine и BM25 не смешиваются; порог отказа из этих распределений не выводится. Empty gold не требует semantic empty.

## Доказательства и дальнейшая приёмка

Исходный JSON сохранён полностью, включая top-10 и временные samples. Агент независимо пересчитал 350 query-строк, 152 intent-строки, 58 сводок class/tag/group, 156 пороговых флагов, 350 строк fragment diagnostics, 35 exact/ambiguous/negative контролей, 1908 временных samples и 30 negative scores. Расхождений не обнаружено. Членство annotation/large/owner/module срезов не было полностью реконструировано из сохранённого JSON; требуется исходный набор или runner для воспроизведения.

Повторяемость измеренной версии подтверждена выполненными guards, но отдельные rankings повторов в старом результате не сохранены; независимо повторить их сравнение по JSON нельзя. Три build/load samples старый runner сохранял отсортированными. Новый runner хранит хронологические samples и журнал попыток; прежний результат не переписан.

Оригинальная измерительная версия прекращала расчёт при ошибке запроса; успешный результат не доказывает ошибочные ветви. Репозиторный перенос добавляет сохранение ошибок и not_run, без изменения формул, порогов или gold. Model run новой версии требует отдельного решения; CI не скачивает модель.

Репозиторный local gate, PR CI, review, merge, post-merge CI и SYNC фиксируются отдельными доказательствами на соответствующих SHA. Этот отчёт не объявляет их выполненными. Runtime/API, production default, наборы и схемы runtime не меняются. Следующие границы — #371 (поведение) и #372 (покрытие и документация), без автоматического запуска.

END: search_quality_issue329_v1
