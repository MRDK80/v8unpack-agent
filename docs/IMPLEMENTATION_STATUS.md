# Статус реализации

## Реализовано

### scan_forms
- Config-layout: 4-уровневый (`<Тип>/<Объект>/<Контейнер>/<Форма>/`) и
  3-уровневый (`CommonForm/<Форма>/`) — issues #9, #13
- External-layout: `External/<объект>/<Form|ReportForm>/<форма>/` — issues #25, #32
- Elem-only формы (управляемые без `.obj.bsl`) — issues #55, #57
- `bsl_sha256` в FormEntry — issue #38
- `elem_sha256` в FormEntry — issue #40
- `elem_json_path` (relative-to-root) в FormEntry — issue #57

### drift_checker
- `check_drift()` с `DriftReport`: added / removed / modified /
  stale_extractions / structure_modified — issues #10, #18, #38, #40
- Hash-based `modified` detection — issue #38
- Elem-only формы исключены из removed/stale, включены в structure_modified
  — issue #58
- **`check_drift(mode="external")`**: корректная поддержка external-layout
  через делегирование `_disk_snapshot` → `scan_forms(mode=mode)` — issue #73.
  Проверено на 14 реальных внешних формах (ExternalDataProcessor + ExternalReport).

### catalog_resolver
- `resolve_data_path(data_path, object_json)` — best-effort резолюция строки
  `data_path` через JSON объекта (`Catalog.json` и др.); поддерживает пути
  `Объект.Реквизит` и `Объект.ТЧ.Реквизит`; при отсутствии файла или
  нераспознанном пути возвращает `resolved=False` без исключений — issue #76,
  PR #83.
- `object_json_path(form_entry)` — определяет путь к JSON объекта по
  `form_entry.form_path` (2 уровня вверх).

### elem_parser — привязка элементов к данным
- `decode_legacy_data_path()` + `is_legacy_form_data()` — декодирование
  `data_path` в **обычных** формах через поле `prop` записи `data`
  (имя реквизита в `raw[4][1]` при теге `"14"`) — issue #85, PR #86.
- Разбор по UUID реквизита оставлен только для управляемых форм; в обычных
  UUID внутри `raw` описывают класс виджета и давали ложные срабатывания.
- Для управляемых форм добавлен консервативный структурный fallback после
  UUID: точное имя реквизита формы (с рекурсивным обходом вложенных `props`)
  и колонка таблицы через непосредственного родителя. Префиксные эвристики
  не применяются — issue #85, PR #86.
- `_merge_source_duplicates()` — схлопывание одноимённых записей `data`/`props`
  в одну (приоритет `data`, слитые источники в `merged_sources`).
- Проверено на `Catalog/Банки`: `ФормаЭлемента` — 8 привязок из 20 элементов,
  `ФормаСписка` — 4 из 7, `ФормаЭлементаУправляемая` — 11 из 19, warnings 0.
- Аудит структурного fallback на реальной выгрузке: 3292 из 4549 исходно
  неразрешённых Field-записей безопасно восстановлены, 1257 оставлены без
  догадок; 81 дополнительное точное совпадение подтверждено во вложенных
  `props`. Метрика относится только к проверенной выгрузке.

### Прочее
- `elem_parser.parse_elem_json()` — единственный парсер `*.elem.json` — issue #40
- `form_summary.build_form_summary()` — семантическая выжимка формы — issue #66
- `managed_forms.discover_elem_forms()` — обнаружение elem-only форм — issue #55
- `form_router` — маршрутизация запросов к формам по типу/имени

## Не реализовано / В планах

- `decode_object_attributes` (#84) — декодирование реквизитов объекта из
  raw-секции `header` в `Catalog.json`; без этого `catalog_resolver`
  возвращает `resolved=False` на реальных выгрузках.
- `form_context` (#77) — материализация `FormEntry → FormContext`
  (`BSL + FormSummary + resolved bindings + to_llm_prompt_fragment`);
  ожидает результатов #84 (`data_path` уже доступен после #85).
- CLI для `check_drift` (аналогично `scan_forms --mode`)
- Детекция дрейфа по `form_summary` (семантический уровень)
- Инкрементальный baseline (обновление только изменённых форм)

## Известные ограничения

- `elem_sha256` вычисляется только при наличии `*.elem.json`; формы без него
  не участвуют в `structure_modified` (не баг, дизайн).
- Вложенность групп в `elem_parser` не реконструируется полностью —
  хэш строится по достоверной части дерева.
- `catalog_resolver` реализует best-effort слой резолюции; полнота на реальной
  выгрузке зависит от декодирования реквизитов объекта — #84.
- В управляемых формах отсутствие UUID может компенсироваться только точным
  структурным подтверждением через `props` или непосредственного
  родителя-таблицу. Префиксные совпадения намеренно не угадываются.
- #84 требуется для декодирования типа, длины и синонима реквизита из raw
  `header`; она не расширяет структурные эвристики `data_path`.
- Отсутствие `data_path` у надписей, групп, страниц, разделителей и панелей
  команд — штатный результат, а не пробел декодирования.
