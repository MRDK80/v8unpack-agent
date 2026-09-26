# Единая граница санитизации диагностики (issue #142)

Диагностический текст, который покидает пакет, проходит через одну функцию:
`v8unpack_agent._safe_paths.sanitize_diagnostic`. Она применяется к уже
сформированному выходному тексту. Исходная выгрузка, BSL-код и внутренние
структурированные результаты не меняются.

## Контракт

| Свойство | Поведение |
|---|---|
| POSIX absolute path | `/<...>/<хвост>` заменяется на `.../<хвост>` |
| Windows drive path | буква диска с прямым или обратным слэшем, включая удвоенные обратные слэши, заменяется на `.../<хвост>` |
| UNC path | два обратных слэша (или экранированные четыре) либо `//` перед `server/share`; сервер и share отбрасываются |
| Домашний каталог | `~`, `home/<имя>`, `Users/<имя>` и `root` в начале пути отбрасываются вместе с именем |
| Хвост | не больше `SAFE_TAIL_SEGMENTS` (4) последних сегментов, разделитель `/` |
| Относительные пути | не меняются: `Catalog/Obj/CatalogForm/Form`, `./x` |
| Машинные коды | не меняются: `[code=FORM_SCAN_ERROR]`, `status` и `reason` из #141 |
| Детерминированность | чистая функция без обращения к ФС и окружению |
| Идемпотентность | повторная санитизация результата возвращает его же |
| Fail-closed | любой сбой, включая сбой `str(obj)`, даёт `SANITIZER_FAILED_TEXT` с кодом `sanitizer_failed`; исходная строка не возвращается |

Путь распознаётся по форме записи, а не по заранее известному корню, поэтому
результат не зависит от ОС, на которой запущен код.

## Каналы

| Канал | Вид | Точка применения |
|---|---|---|
| `FormScanIndex.scan_warnings`: `error scanning`, `cannot import` | public API | `scan_forms`, при формировании текста исключения |
| logger `v8unpack_agent.scan_forms` | log | тот же уже санитизированный текст |
| `FormScanIndex.to_dict()`, `save()`, `FormRouter.reindex()` | persisted artifact | `scan_warnings` и `FormEntry.warnings` при сериализации |
| CLI `scan_forms`: `parser.error`, «Индекс сохранён» | stdout/stderr | `scan_forms.main` |
| `FormContext.metadata["warnings"]` | public API | `build_form_context` |
| `to_llm_prompt_fragment`: заголовок, JSON `## SUMMARY`, строки `data_path` из #141 | LLM | до сборки и обрезки фрагмента |
| `RunOutcome.scan_warnings` | public API | `run_pipeline` |
| `ObjectRunResult.message`, `RunFatalError.message` | public API, persisted artifact | `runner._safe_message`; валидатор `run_report` отклоняет текст, который sanitizer изменил бы |
| Непредвиденное исключение прогона | public API, persisted artifact | `run_pipeline`: `fatal_error.reason_code = internal_error` |
| CLI `v8unpack-agent-run`, stderr | log | ошибка записи отчёта; `internal_error` без traceback и без `str(exc)` |
| `FormsIndexEntry.warnings` | persisted artifact | `pipeline.update_forms_index` |
| `safe_error_text` (`elem_parser`, `object_decoder`) | все каналы выше | итог проходит через `sanitize_diagnostic` |

## Вне границы

- BSL-текст и реквизиты объекта во фрагменте: это данные выгрузки, а не
  диагностика. Поиск персональных данных в BSL не выполняется.
- Полноценный DLP, санитизация выгрузки на диске, формат распаковки.
- Сегмент пути с пробелом распознаётся только до первого пробела.
- Имя пользователя вне `home`, `Users` и `root` не распознаётся: например,
  произвольный каталог первого уровня с именем пользователя.
