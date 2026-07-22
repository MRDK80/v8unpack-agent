# Выжимка управляемой формы: build_managed_form_summary

Issue #66 (PR #68). Модуль `v8unpack_agent/managed_form_summary.py`.

## Концепция

Отдельного адаптера реального `*.elem.json` **нет**. Канонический разбор делает
`parse_elem_json(form_root) -> ElemIndexResult` (модуль `elem_parser`), а
`build_managed_form_summary` строит семантическую выжимку поверх его результата.

```
parse_elem_json(form_dir) -> ElemIndexResult
  \_ build_managed_form_summary_from_elem_index(result) -> ManagedFormSummary
       \_ to_normalized_json(summary) -> детерминированный JSON
```

> Ранняя промежуточная реализация с `managed_form_adapter.adapt_elem_json(raw)`
> удалена (коммит `d10c382`, «superseded by parse_elem_json»): она давала
> `relations = 0`. Единственный парсер `*.elem.json` — `parse_elem_json`.

## API

```python
from pathlib import Path
from v8unpack_agent import build_managed_form_summary, to_normalized_json

# принимает КАТАЛОГ формы (не raw-dict); внутри вызывает parse_elem_json
summary = build_managed_form_summary(Path("path/to/Form/<form_dir>"))
print(to_normalized_json(summary))
```

| Функция | Назначение |
|---------|------------|
| `build_managed_form_summary(form_dir)` | Принимает каталог формы, внутри вызывает `parse_elem_json`, возвращает `ManagedFormSummary`. |
| `build_managed_form_summary_from_elem_index(result)` | Строит выжимку, если `ElemIndexResult` уже получен. |
| `to_normalized_json(summary)` | Детерминированный вывод (`sort_keys=True`, `ensure_ascii=False`) для diff/drift. |

## Бакеты ManagedFormSummary

`attributes` / `commands` / `elements` / `events` / `relations` / `warnings` —
семантическое ядро формы без layout-шума и GUID; основа для diff-сравнения форм
и drift-контроля (перестановка GUID / сдвиг координат не меняют результат).

## Границы и переносимость

- Пути через `pathlib`, чтение UTF-8 явно; OS-нейтрально (POSIX `/` и NT `\`).
- Пустой summary тогда и только тогда, когда `tree` / `props` / `data`
  в `*.elem.json` пусты одновременно — легитимное поведение, не дефект.
- Извлечение текста модуля формы — вне scope (лежит в отдельном файле).

## Связь с пайплайном

```
scan_forms (#57)          -> регистрирует elem_json_path в FormScanIndex
  \_ parse_elem_json       -> канонический разбор *.elem.json (ElemIndexResult)
       \_ build_managed_form_summary -> семантическая выжимка формы
```
