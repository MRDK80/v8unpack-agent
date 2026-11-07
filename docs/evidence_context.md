# Контекст фактов и evidence

`v8unpack_agent.evidence_context` — opt-in library API схемы
`evidence_context/1`, поверх [BSL-фактов](bsl_facts.md). JSON Schema:
[schemas/evidence_context_1.json](schemas/evidence_context_1.json), Draft 2020-12.
Схема проверяет форму, не истинность и не семантическую безопасность.
CLI/каталог команд, canonical source, ModuleEntry, embeddings и старые
legacy/framed не изменяются. MCP-сервера в проекте нет.

## Клиент и граница полномочий

```python
from pathlib import Path
from v8unpack_agent.evidence_context import build_module_evidence

# Доверенный host выбирает root/index/module_id и задачу вне текста модели.
context = build_module_evidence(Path("export"), index, module_id)
payload = context.to_dict()
# payload доставляется как данные, инструкция клиента — отдельно.
# Ответ модели не выполняется: перед эффектом host проверяет task-contract.
```

Само значение `value_semantics="untrusted_data"` не заставляет модель
соблюдать границу. Имена, пути, module/source/fact id и значения литералов
остаются недоверенными данными. Совпавший хеш не является подписью автора,
разрешением писать файл или правом расширить scope. JSON не получает
полномочий system/developer. Python-объекты создаёт host, они не принимаются
из самоописания модели как доказательство.

Публичные имена: `EVIDENCE_CONTEXT_SCHEMA`, `EvidenceContext`,
`EvidenceModule`, `build_module_evidence`, `build_form_evidence`,
`build_search_evidence`, `evidence_fragment`.

## Операции и поля

`build_module_evidence(root, index, module_id, *, operation="exported_functions",
declaration_name=None, max_facts=1000, max_chars=2_000_000)` выбирает полный
индексированный BSL-снимок через `analyze_indexed_module`.

- `exported_functions`: только исходные имена экспортных функций и evidence.
  Нерелевантные комментарии/литералы в выдачу не входят. Пустой подтверждённый
  список отличается от невозможности анализа.
- `returned_string`: требует отдельного имени функции, выбранного host.
  Возвращает точный прямой литерал в поддержанном подмножестве #481.
  Инструкции внутри необходимого литерала сохраняются как данные.
  Отсутствующая функция — incomplete; вычисляемое тело — unsupported.

Envelope содержит enum-поля `schema`, `surface`, `operation`, `status`,
`scope`, `coverage`; отдельно — `modules` и коды `omissions`.
Scope — **выбранные модули**, а не все объекты конфигурации.
Каждая карточка модуля содержит extractor version, статус/причину, source
metadata, facts, total/omitted и diagnostics. В source находятся module id,
относительный путь, source id, хеш байтов/текста и явная нормализация.
В факте — kind, извлечённое имя, fact id и включительный диапазон строк с 1;
у returned_string также value. Source metadata и версия распространяются
на все факты этой карточки. Полный BSL в JSON не включается.

`max_facts` — неотрицательный лимит на модуль. Усечение даёт модульный
`truncated` и `facts_omitted`; общий status — incomplete. Неподдержанный,
неполный или недоступный источник не заменяется полным пустым списком.
Diagnostics содержит BOM и счётчики Cf, не исходные комментарии. Это
диагностика символов, не detector prompt injection. Ограничение анализа
`max_chars` и нормализация определены в bsl_facts; данные не переписываются.

## Три поверхности и согласованный объём

| Поверхность | Покрытие и границы |
|---|---|
| Module | Две операции над полным индексированным BSL-снимком |
| Form | `build_form_evidence(root, index, module_id, **options)` требует `module_kind="form"`; покрывает только BSL формы |
| Search | `build_search_evidence(root, index, context, *, allowed_module_ids, max_modules=10, ...)` выбирает module hits из included существующего SearchContext в scope host и перечитывает полный индексированный модуль |

FormContext.summary, подписи, metadata и реквизиты формы не превращаются
в доказанные факты. `coverage.form_structure="unsupported"` сохраняется
явно, включая form-поверхность. Полного form/metadata evidence здесь нет;
это ограничение и потеря покрытия, а не безопасность за счёт отказа.
Успех BSL-операции не означает полноту всех данных формы.

SearchContext и его срезы не изменяются. Содержимое среза не принимается за
полную опись модуля. Включённый hit служит выбором кандидата; единственный
источник фактов — текущие байты индекса. Form/owner/candidate-only hits
не домысливаются в module id: для них omission `unsupported_search_artifact`.
Out-of-scope, module budget и неполная исходная search selection отражаются
в omissions и общем truncated. Одна карточка на module id. Полнота выбранных
модулей не означает полноту поискового recall или всей конфигурации.

## Отдельный сырой evidence

`evidence_fragment(snapshot, *, source_id, start_line, end_line,
max_chars=10000)` работает только с уже выбранным host снимком, без чтения
файлов. Проверяет source id и допустимость диапазона. Возвращает отдельную
схему `evidence_fragment/1` со source metadata, диапазоном, хешем точного
среза и обратимым framed блоком. Декодирование блока восстанавливает срез
без изменения Unicode/BOM/CRLF. Диапазон `source_range` использует координаты
сканера CR/CRLF/LF; вложенный framed блок имеет собственные локальные
LF-координаты. При превышении бюджета возвращается truncated без текста,
а не якобы полный срез. Полный исходник остаётся доступен host в snapshot.

Предоставление raw evidence может вернуть атакующие инструкции в контекст
модели. Оно не повышает их доверенность. Снимок и fact id не устраняют
TOCTOU; актуальность и разрешение действия обязан проверять исполнитель
перед эффектом. Произвольные бизнес-утверждения не валидируются этой схемой.

Тесты: `tests/test_evidence_context_issue482.py` — сохранность исходных
байтов/индекса/поиска, минимальная выдача, необходимый литерал, независимые
диапазоны/хеши, stale snapshot, бюджеты и form/search ограничения. Старые
transport-эталоны остаются частью полного гейта. Held-out не запускается.
