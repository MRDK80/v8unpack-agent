# Reference-клиент Qwen/Llama

[examples/agent_reference_client.py](../examples/agent_reference_client.py)
использует [task-contract/validator](action_validation.md) и отдельный
[контекст фактов](evidence_context.md). Это локальный reference host,
не hosted/MCP service и не новый CLI продукта. Установка через PyPI
не включает каталог examples: запускайте пример из клона репозитория.
Импорт/`python -m examples.agent_reference_client` не вызывает модель и
не пишет данные; реальный запрос выполняет явно вызывающий host.

## Три явных режима

| Режим `run_reference` | Поведение |
|---|---|
| `deterministic` | Статический анализ → проверка → report.json; LLM не вызывается, profile не приписывается результату |
| `native` | Модель читает факты через read_source и запрашивает save_report; host проверяет отчёт через #483 |
| `reader` | Модель получает исходник в framed без tools; её ответ остаётся недоверенным и никогда не попадает исполнителю |

Нет скрытого fallback между режимами, парсинга команд из обычного текста
или обходного writer. Native Llama exports — явный NO-GO ADR #480, без
вызова модели/записи. Предварительно пригодные Qwen и Llama query тоже
требуют calibration готового клиента: по умолчанию native возвращает
`profile_task_not_admitted`. Host передаёт `admitted=True` только после
проверки отдельного результата профиля/задачи; эта опция не приходит от
модели и не заменяет разрешение пользователя на конкретный эффект.
`calibration=True` — явно исследовательский допуск кандидата, не default
и не подтверждение пригодности. NO-GO Llama exports им не обходится.

```python
from pathlib import Path
from examples.agent_reference_client import LocalOllama, run_reference
from v8unpack_agent.action_validation import create_report_contract

# Host выбирает root/index/module_id, имеет разрешение на эффект и
# обеспечивает неизменность source workspace до commit (см. #483).
contract = create_report_contract(root, index, module_id)
client = LocalOllama()  # только loopback HTTP, без proxy и redirect
runtime = client.verify("qwen25")  # фиксированные version/digest; без download
result = run_reference(contract, root=root, output_dir=Path("reports"),
    mode="native", profile="qwen25", seed=17, chat=client.chat,
    admitted=host_verified_profile_task_admission)
```

Output directory — выделенный host каталог вне дерева выгрузки, фиксированный
файл report.json. Его адрес не передаётся модели. Если filesystem не
поддерживает атомарную no-clobber публикацию, действие не считается успешным.
`admitted` — решение host, а не защищённый capability token для недоверенного
Python-плагина. Библиотека не может запретить независимо выданные shell tools.

## Граница чтения и исполнения

В native доступны ровно read_source и save_report. Описание/parameters
save_report согласованы с задачей: только function_names (полный массив)
или query (точное значение). Значения module/source/evidence выбирает и
прикрепляет host из собственного контракта; модель их не объявляет
доказанными и не выбирает новый scope. Затем #483 независимо извлекает
факты и проверяет фактические аргументы модели. Лишний ключ, включая path,
не разрешается. Требуется настоящий read_source до save_report. Весь пакет
multi-call отклоняется до чтения/записи; plain text «save_report выполнен»
не считается вызовом или полезным успехом. Повторной записи после commit нет.

Reader получает точный выбранный сырой снимок как framed, без инструмента
записи/сети/shell. Это уменьшение полномочий, а не доказательство истинности
ответа. Даже JSON/инструкция в ответе не превращается в команду. В рамках
calibration его содержимое сравнивается с независимым эталоном для двух
конкретных синтаксических задач; произвольная семантическая проза этим
сравнением не проверяется. Ограничения parser, form metadata и TOCTOU из
предыдущих API сохраняются.

## Фиксированные runtime и инструкции

Профили в `PROFILES`: qwen2.5:7b, llama3.1:8b (384 generated tokens),
qwen3:8b без/с thinking (4096). Все: temperature 0.3, num_ctx 8192,
seeds 17/29/43/61/83, максимум 4 model steps, timeout 180 секунд на запрос.
Reader — один запрос без tools. Digest каждого веса закреплён в исходнике,
Ollama — 0.16.1. `LocalOllama.chat` требует предварительного verify.
Никакие веса/ключи не скачиваются автоматически. HTTP только loopback,
редиректы и proxy отключены. Endpoint записывается только в host настройке,
в публичных traces нет приватных путей/содержимого.

`SYSTEM` и `READER_SYSTEM`, tool descriptions/schemas, budgets и policy
фиксируются **до первого нового ответа**. Native инструкция отличается
от старого эксперимента #479: явно описывает evidence_context, host validation,
один вызов за ответ и конкретную операцию. Query description исправлена до
этой серии и согласована с параметром query. Это новый комбинированный
клиент, не чистая ablation одного framed и не повтор прежней инструкции.
Исторические #479/#480 и их результаты не изменяются.

## Воспроизводимая calibration

Протокол/шесть синтетических clean/benign-lookalike случаев:
[research/agent_client_issue484/protocol.json](research/agent_client_issue484/protocol.json),
[cases.json](research/agent_client_issue484/cases.json). Это development,
не held-out. План: 246 строк — 6 deterministic, 120 native (15 Llama exports
явно unsupported без модели), 120 reader. Всего 225 model trajectories.

До запуска: source commit → freeze seal с хешами всех Python модулей пакета,
клиента, исполнителя, runner, cases/protocol → публикация seal/плана в #484.
После первого ответа исходники/prompts/schemas/cases/policy не меняются.
Результаты и решения добавляются отдельно. Порог native per profile/task:
correct>=95%, никаких no_report/ошибок/лимитов/неверных commit. По 15 строк
на группу это требует 15/15. Нативный Llama exports сохраняет NO-GO.
Проверка attack utility и парной latency относительно legacy — в #485;
calibration не повышает профиль в универсально безопасный/default.

Из корня клона (с установленными dev/test dependencies):

```bash
python -m scripts.agent_client_calibration_issue484 freeze
python -m scripts.agent_client_calibration_issue484 run --output calibration-output
python -m scripts.agent_client_calibration_issue484 audit --output calibration-output
```

Выходной каталог run должен быть новым. Runtime проверяется до генерации;
resume требует неизменных среды и seal, replay уже записанного префикса,
`--resume` продолжает без перезапуска завершённых строк. Файл PAUSE внутри
output останавливает run перед следующим trial; удалите его перед resume.
Оборванный JSONL/незавершённый trial не перезапускается молча.
Все запросы/ответы, native events и фактические файлы сохраняются; offline
audit воспроизводит исполнителя с записанными ответами и независимо сверяет
отчёты с эталонами. Для межверсий Python отличается только декларативная
версия Unicode diagnostics: audit проверяет её по закреплённой среде и
сравнивает остальной запрос/данные точно. Поле исходного текста не меняется.

Тесты `tests/test_agent_reference_client_issue484.py` работают с synthetic
callbacks, включая полный mock/replay плана и подмену файла; CI не обращается
к модели/сети. Traces реальной серии и допуск публикуются после её завершения.
