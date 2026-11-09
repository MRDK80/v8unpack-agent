# Компактные факты одной BSL-задачи

`v8unpack_agent.task_evidence` — отдельный opt-in API над существующим
[evidence_context/1](evidence_context.md). Публичные имена:
`TASK_EVIDENCE_SCHEMA`, `TaskEvidence`, `build_task_evidence`.
Новая схема — `task_evidence/1`, старые API/CLI/индексы/default не меняются.
Это проекция фактов, а не авторизация пользователя и не обещание устойчивости LLM.

Существующий evidence уже исключает комментарии и ненужный исходный текст.
Новый вид убирает вложенность modules, display path/module labels и диагностические
сводки, которые не нужны двум поддержанным операциям. Полные данные остаются у
host. Необходимые имена/литералы сохраняются точно, даже если выглядят как инструкция.
Никаких blacklist, удаления SYSTEM/DEMO_OVERRIDE или изменения исходника нет.

```python
from v8unpack_agent.task_evidence import build_task_evidence
from examples.validated_report_executor import ReportExecutor

# root/index/module_id выбирает host вне модели.
view = build_task_evidence(root, index, module_id)
context_for_model = view.to_dict()
# Неполная проекция здесь вызовет ValueError, а не пустой полный отчёт.
contract = view.report_contract()
executor = ReportExecutor(contract, root=root, output_dir=output_dir)
# Все native proposals поступают единственному executor по прежнему контракту.
```

`operation="exported_functions"` перечисляет экспортные функции;
`operation="returned_string", declaration_name="QueryText"` возвращает
поддержанный прямой литерал. Грамматика, динамические конструкции и полнота —
по [bsl_facts](bsl_facts.md). Структура форм/metadata и произвольные патчи не добавлены.
Скоуп — один выбранный индексированный BSL-модуль, включая BSL формы.

## Машиночитаемый контракт

`to_dict()` возвращает новый словарь:

- `schema`, `operation`, `scope_kind="selected_bsl_module"`;
- `status`: complete/incomplete/unsupported/truncated, `reason`;
- `value_semantics="untrusted_data"`;
- `source_id`, `bytes_sha256`, `text_sha256`, `normalization` (null при отсутствии
  подтверждённого источника), `extractor_version`;
- `facts` с прежними kind/name/value/fact_id/start_line/end_line;
- `facts_total`, `facts_omitted` — явная полнота без скрытого drop.

Source ID связан также с исходным module/path у host, но сам hash не является
подписью доверенного автора. Операцию, разрешение и адрес записи он не задаёт.
Имена и literal values в facts — недоверенные данные, не инструкции.
`can_report` истинен только при complete и привязке к исходным байтам.
`max_facts`/`max_chars` не меняют грамматику; нехватка бюджета не равна пустой описи.

`report_contract()` сначала запрещает неполную проекцию, затем заново читает
индексированные байты через прежний create_report_contract и сверяет snapshot ID.
Изменение файла/index mismatch блокирует создание контракта. Запись по-прежнему
выполняет отдельный host executor с [независимым validator](action_validation.md).
Потребитель обязан применять этот gate для данного view; библиотека не может
запретить стороннему host игнорировать его и создать иной контракт самостоятельно.

Дополнительно `max_fact_chars=20000` ограничивает сериализованный массив фактов
в символах JSON, не в токенах модели. При превышении факты не отдаются,
status=truncated/reason=fact_char_budget, facts_omitted=facts_total; contract
не создаётся. Служебный envelope отказа остаётся даже при нулевом бюджете.
Полный пустой набор остаётся complete. Host выбирает лимит под своё окно
контекста; этот параметр не гарантирует лимит токенов произвольной модели.

## Дополнительное чтение

`view.read_fact(fact_id, source_id=..., start_line=None, end_line=None,
max_chars=10000)` не читает файловую систему. Он берёт только включённый fact
из immutable snapshot. Без endpoints возвращает его диапазон; с endpoints требует
оба целых номера внутри диапазона fact. Другой source/fact/path, bool-координаты
и выход за границы отклоняются. Произвольного пути и чтения соседнего модуля нет.

Ответ `task_evidence_fragment/1` содержит source hashes, fact ID, диапазон,
status/reason, text_sha256 и обратимый framed. При нехватке бюджета framed=null,
status=truncated; молчаливого сокращения нет. Опциональный raw fragment остаётся
недоверенным и не повышает полноту ранее усечённой описи. Для полной описи host
должен явно перестроить проекцию с подходящим бюджетом.

Путь не сериализуется и в fragment metadata. Исходник с CRLF/Unicode декодируется
обратно без потери включённых значимых символов. Снимок может устареть относительно
внешнего файла: чтение сохраняет старый source ID, а контракт требует повторной
проверки. Эксклюзивность/TOCTOU — прежнее ограничение, отдельно R13 (#499).

## Применение и оценка

Python-объект создаёт доверенный host, не JSON модели/плагина. Сам объект не OS
capability и не sandbox. Дополнительный read API проверяется детерминированно;
новый native tool binding не объявляется автоматически. Допуск модели определяется
отдельным profile/task испытанием. Исследование #496 сравнивает проекции с тем же
validator; статический факт уже извлечён программой, его вычисление не приписывается LLM.
