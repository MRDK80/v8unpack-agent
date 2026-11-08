# Статические реквизиты управляемой формы (#500)

Opt-in модуль `v8unpack_agent.form_evidence`: строгий контракт для **имён
объявленных статических реквизитов верхнего уровня одной выбранной формы**.
Это отдельное расширение; старые `evidence_context/1` и BSL-поверхность формы,
legacy/framed/CLI/индексы не меняются. Старые form_fields `unsupported` из #485
не переинтерпретируются как поддержанные.

## Поддерживаемый источник

Настоящая файловая выгрузка v8unpack: `<stem>.json` metadata +
`<stem>.elem.json` projection, обнаруживаемые существующим `discover_elem_forms`.
`Form.xml` и искусственный `attributes[*].name` не являются этим входом.
Host безопасно выбирает/читает одну пару и задаёт namespace `form_id` вне модели.
API принимает immutable bytes, сам не читает файлов и не даёт разрешений чтения.
Пути discovery и metadata-флаги не являются полномочиями; нельзя разрешать
модели выбирать произвольные файлы вместо выбранной host пары.

Минимальный проверенный layout:

| Поле | Требование |
|---|---|
| metadata `Тип формы` / `Версия элементов формы` | Строки `1` / `1` |
| `metadata.form[0][0][0]` | Root layout `3` или `4` |
| `metadata.form[0][0][3][1]` | `0` для подтверждённой пустоты; `Дочерние элементы отдельно` для непустой проекции |
| elements `props` | Полный плоский list; null/[] только с подтверждённым zero counter |
| Каждый prop | Ровно `name`, `id`, `raw`; непустое UTF-8 имя, уникальные имена и decimal IDs |
| `raw` | 16-slot brace list, tag `9`, ID в `[1]` равен `[id]`, quoted `[3]` точно совпадает с `name`, `[13] == "0"` |

Позиции соответствуют первичному
[FormElements4/FormProps](https://github.com/saby-integration/v8unpack/blob/deba8926fab78f67b8c362c010397a38967a2820/src/v8unpack/MetaDataObject/Form/FormElements4/FormElement.py).
Его байты совпали с установленным v8unpack1.2.13: sha256
`8c0527158242b3442c7404344cc65b07f370229c97507ef11bd2b4c3806917d7`.
Это проверенный layout сериализованных артефактов, не официальная полная
спецификация платформы или общий классификатор ordinary/managed.

Upstream best-effort decode может вернуть null после ошибки. Поэтому один
`props is None` не доказывает пустоту. Несогласованный counter/raw/name/id,
duplicate JSON keys/names/IDs, truncated/corrupt/invalid UTF-8/nonfinite JSON,
missing обязательные поля дают incomplete. Сначала выполняется ограниченная
строгая preflight-проверка; затем повторно используется существующий elem
parser `_extract_props` с проверкой точного числа/порядка имён. Silent omission
и best-effort summary не принимаются как полная опись.

Вложенные реквизиты/колонки, ordinary/unknown layout дают unsupported; отсутствие
файла — unavailable. Поддержка типа значения реквизита, UI-дерева, owner attributes,
динамических изменений, ссылок и runtime здесь не заявляется. Другие части формы
могут остаться неизвестными: полнота **только выбранной статической проекции**.
Согласованные JSON-поля/хеши не доказывают подлинность, полноту original CF или
возможность загрузить произвольное adversarial имя обратно в конфигуратор.

## API и evidence

Публичные имена: `FormSource`, `FormFact`, `FormEvidence`, `FormContract`,
`FormValidation`, `analyze_form`, `make_form_report`, `validate_form_report`.
Корневые lazy exports пакета не расширяются; импортируйте из подмодуля.

```python
from v8unpack_agent.form_evidence import FormSource, analyze_form

# metadata_bytes/elements_bytes безопасно выбраны и ограничены host;
# None означает недоступный файл, а не пустой объект.
source = FormSource("selected-form", metadata_bytes, elements_bytes)
evidence = analyze_form(source)
payload = evidence.to_dict()
if evidence.status == "complete":
    contract = evidence.contract()
```

`FormSource` хранит только immutable bytes (до 2MB на файл) и host namespace.
Source ID зависит от namespace и хешей обоих файлов. Нормализации значимых имён,
Unicode, порядка и дубликатов нет; дубликат отклоняется целиком. Файловый BOM
допускается как encoding, BOM/Cf внутри имени остаются точными данными.
Парсинг ограничен глубиной40/200000 decoded nodes. `analyze_form(max_facts=1000,
max_bytes=2000000)` допускает уменьшение бюджетов: усечение всегда incomplete,
с `total_facts`/`omitted`; такой evidence не создаёт full-report contract.

`static_form_evidence/1` содержит operation `declared_form_attributes`, source,
status/reason, coverage и facts. Coverage различает static_top_level_attributes,
full_form=`unknown`, runtime/nested=`unsupported`. Каждый факт имеет исходное
имя, attribute ID, fact ID и JSON pointers к `/props/i/name` и `/props/i/raw/3`
артефакта elements. Все facts связаны с одной парой source hashes; одинаковые
имена разных host форм не смешиваются. Хеши и `value_semantics` не авторизация.

## Независимая проверка и эффект

`validate_form_report(contract, report)` повторно извлекает имена из host bytes,
не доверяя cached facts или supplied evidence. Строго допускается только
`{"attribute_names": ["..."]}` с точным полным исходным порядком. Лишние keys,
paths/source/role/tool claims, неверные типы/имена/порядок отвергаются.
`make_form_report` — детерминированный ответ без LLM, не model success.

[`FormExecutor`](../examples/form_report_executor.py) — отдельный opt-in
reference host. Model dispatcher получает один `save_form_report` с host-bound
request_id/report; связать ответ конкретного RPC с этим request обязан host.
В эксперименте модель предлагает **только report**: host добавляет собственный
request ID, не копирует authority/path/source из ответа. Сам request ID не secret
и не capability. Host отдельно выбирает исходник/операцию/output и удерживает
одноразовый in-memory validation ticket. Cached evidence не создаёт полномочий.

Publication primitive переиспользуется из R13 без изменения его commit/cleanup
алгоритма; все41R13 fault/regression сохраняются. Единичный полный
`form_snapshot_report/1` содержит request, source, operation, coverage, точные
facts/pointers и report. Содержимое остаётся **недоверенными данными**, даже если
проверено как истинное. Нельзя пересылать его как system/developer или разрешение
следующему агенту.

Эффект реализован только на Linux/private local FS; остальные платформы
unsupported до IO. Нет raw writer/shell/network. Atomic no-clobber сохраняет
старый report; snapshot-only результат не обещает current-file-correct.
Все [границы R13](snapshot_report_executor.md) — same-uid/host/OS/IO/cleanup/
durability/currentness — сохраняются. Это не sandbox против arbitrary Python.

## Проверка модели и ограничения

Новый development-протокол —
[план](research/form_evidence_issue500_plan.md) и sealed артефакты
`docs/research/form_evidence_issue500`. Прежняя усиленная **READER_SYSTEM**
read-only ветки #484/#485 используется побайтно, одинаково для четырёх профилей;
это не R11 инструкция выбирать refs. Новый task/schema соответствует реальной
статической проекции. Один JSON proposal, без recovery/native fallback.
Такое сравнение не изолирует причинный эффект prompt/schema против R11/R12.

Отдельно учитываются deterministic no-LLM, clean, attack, incomplete/unsupported/
unavailable, actual effects, token/time limits и неизвестный usage. Все parsed
provider responses сохраняются losslessly в `response_json` (это JSON snapshot,
не исходные HTTP wire bytes), включая неизменённые content/thinking. Неверная
запись и отсутствие полезного результата различаются. Development GO не означает
native/default/production admission; новые acceptance данные остаются для #502.

Фактический [результат R14](research/form_evidence_issue500.md):378строк/240RPC,
0wrong writes, но все4model profiles NO-GO по utility. Reported token counters
не объявляются полным измерением thinking cost. Все original responses/effects
сохранены и воспроизводятся pinned-source helper без модели.
