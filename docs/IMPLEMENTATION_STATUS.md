# Статус реализации

> Обновлено: 2026-07-23

## Легенда

| Символ | Смысл |
|---|---|
| ✅ | Реализовано, тесты зелёные |
| 🔄 | В процессе (открытый PR) |
| ❌ | Не реализовано |
| ⚠️ | Реализовано частично / есть known issue |

---

## Сканирование форм (`scan_forms`)

| Функция | Статус | Issue / PR |
|---|---|---|
| 4-уровневый config layout (Catalog/Document/…) | ✅ | #9 |
| 3-уровневый layout (CommonForm) | ✅ | #13 |
| External layout — обработки `.epf` | ✅ | #25 |
| External layout — отчёты `.erf` / ReportForm | ✅ | #32 |
| `bsl_sha256` в FormEntry | ✅ | #38 |
| `elem_sha256` в FormEntry | ✅ | #40 |
| `elem_json_path` в FormEntry (relative-to-root) | ✅ | #57 |
| Elem-only формы без `.obj.bsl` (`include_elem_only`) | ✅ | #55 / #57 |

## Детектор дрейфа (`drift_checker`)

| Функция | Статус | Issue / PR |
|---|---|---|
| `added` / `removed` — config layout | ✅ | #10 |
| `modified` — legacy mtime | ✅ | #18 |
| `modified` — hash-based (bsl_sha256) | ✅ | #38 |
| `stale_extractions` | ✅ | #10 |
| `structure_modified` — hash-based (elem_sha256) | ✅ | #40 |
| Elem-only формы корректно исключены из `stale` / `removed` | ✅ | #58 |
| Elem-only формы участвуют в `structure_modified` | ✅ | #58 |
| `added` / `removed` — external layout (`mode="external"`) | ❌ | #73 |
| `modified` — external layout | ❌ | #73 |
| `stale_extractions` — external layout | ❌ | #73 |

## Парсинг элементов (`elem_parser`)

| Функция | Статус | Issue / PR |
|---|---|---|
| `parse_elem_json` — нормализованное дерево | ✅ | #40 |
| `_compute_elem_sha256` — структурный хэш | ✅ | #40 |
| Фильтрация косметических полей (GUID, координаты) | ✅ | #40 |
| Второй сырой хэш `*.elem.json` | ❌ | не планируется |

## Семантическая выжимка формы (`managed_form_summary`)

| Функция | Статус | Issue / PR |
|---|---|---|
| `build_managed_form_summary` | ✅ | #66 / PR #68 |
| `build_managed_form_summary_from_elem_index` | ✅ | #66 / PR #68 |
| `to_normalized_json` | ✅ | #66 / PR #68 |

## Управляемые формы (`managed_forms`)

| Функция | Статус | Issue / PR |
|---|---|---|
| `discover_elem_forms` | ✅ | #55 |

## Открытые issues (продакшн-баги)

| Issue | Описание | Приоритет |
|---|---|---|
| [#73](https://github.com/MRDK80/v8unpack-agent/issues/73) | `_disk_snapshot` не поддерживает external-layout → ложный `removed` для всех внешних форм | High |

## Открытые PR

| PR | Описание | Статус |
|---|---|---|
| [#72](https://github.com/MRDK80/v8unpack-agent/pull/72) | fix: #58 elem-only формы в drift_checker | 🔄 250 тестов ✅, ожидает merge |

## Верификация на реальных данных (2026-07-23)

Проверка `verify_58_diff.py` на конфигурации УТ 10.3:

| Метрика | Значение |
|---|---|
| Всего форм (с elem-only) | 2 216 |
| Обычных форм с `.obj.bsl` | 2 167 |
| Обычных с `elem_sha256` | 1 897 |
| Elem-only с `elem_sha256` | 45 |
| Elem-only без `elem_sha256` (пустой elem.json) | 4 |
| Ложный дрейф на `main` (stale+removed) | **49 форм** |
| Дрейф на `feat/58` (после baseline) | **0** ✅ |
