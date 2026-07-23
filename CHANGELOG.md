# Changelog

Все значимые изменения фиксируются здесь.
Формат следует [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added
- `FormEntry.bsl_sha256: Optional[str]` — SHA-256 содержимого `.obj.bsl` на момент
  сканирования; используется как основной критерий детекции изменённого кода формы
  в `check_drift()` (issue #38).
- `FormEntry.elem_sha256: Optional[str]` — SHA-256 нормализованного дерева элементов
  формы (`form_elements_index`); используется как независимый критерий детекции
  изменения разметки формы (`structure_modified`) в `check_drift()` (issue #40).
- `DriftReport.structure_modified: list[str]` — новая категория отчёта: ключи форм,
  у которых изменилась структура элементов (дерево `elem.json`) при неизменном BSL
  (issue #40). Учитывается в `has_drift`.
- `FormEntry.bsl_mtime: float` — поле mtime файла `.obj.bsl` на момент сканирования;
  сохраняется как диагностическое поле и legacy fallback для старых индексов без
  `bsl_sha256`.
- `FormScanIndex.load()` — загрузка индекса из JSON с обратной совместимостью:
  старые записи без `bsl_sha256` / `elem_sha256` получают `None`; поведение
  `check_drift()` для таких записей документировано (legacy fallback через
  `bsl_mtime` / тихий пропуск `structure_modified`).
- `DriftReport.modified` теперь **работает**: возвращает ключи форм, чей `.obj.bsl`
  изменился после записи baseline в `FormScanIndex` (issue #18).
- scan_forms: режим `--mode external` для распакованных внешних обработок
  (`External/<имя>/Form/<форма>/Form.obj`); поле `form_elem_path` в FormEntry (#25).
- scan_forms external: поддержка контейнера `ReportForm/` для внешних отчётов;
  `object_type="ExternalReport"` определяется по контейнеру `ReportForm` (#32).
- `v8unpack_agent/managed_form_summary.py`: `build_managed_form_summary(form_dir)`
  + `build_managed_form_summary_from_elem_index(result)` + `to_normalized_json()` —
  детерминированная семантическая выжимка формы (attributes / commands / elements /
  events / relations) поверх канонического `parse_elem_json`. Отдельный слой-адаптер
  реального формата не вводится: `parse_elem_json` — единственный парсер
  `*.elem.json` (issue #66, PR #68).
- **elem-only формы** (`*.elem.json` без `.obj.bsl`) добавлены в `FormScanIndex`
  через `_collect_elem_only_forms` + `discover_elem_forms` (issue #57 / #55).
  Поле `FormEntry.elem_json_path` — relative-to-root путь к `*.elem.json`.
  Подтверждено на 49 формах реальной конфигурации (45 с `elem_sha256`, 4 пустых).
- **`drift_checker._index_snapshot`** теперь возвращает четвёртый элемент —
  `elem_only_keys: set[str]`: ключи форм с `elem_json_path` и несуществующим
  `bsl_path` (elem-only по дизайну). Используется в `_stale_keys` и логике
  `added/removed` (issue #58).

### Changed
- `check_drift()`: при наличии `bsl_sha256` в baseline-индексе использует hash
  как основной критерий `modified`; при отсутствии — legacy fallback через
  `bsl_mtime` (issue #38).
- `check_drift()`: при наличии `elem_sha256` в baseline-индексе заполняет
  `structure_modified`; при отсутствии (старый индекс) — тихо пропускает
  без false-positive (issue #40).
- `FormScanIndex.to_dict()` сериализует `bsl_mtime`, `bsl_sha256`, `elem_sha256`
  в JSON.
- `drift_checker._index_snapshot()` читает `bsl_mtime` из JSON-записи индекса
  вместо прямого обращения к диску через `Path(bsl).stat().st_mtime`.
- scan_forms external: bsl-файл формы ищется по кандидатам `<Container>.obj.bsl`
  (v8unpack 1.2.11) → `<Container>.obj` (legacy), приоритет у `.bsl`; типизация
  для контейнера `Form/` — по имени модуля объекта с fallback
  `ExternalDataProcessor` (#32).
- **`check_drift()` — `keys_with_baseline_elem`** теперь строится по всем ключам
  `index_elem` с непустым хэшем (не только по пересечению с `disk_keys`). Это
  позволяет elem-only формам участвовать в `structure_modified` (issue #58).
- **`check_drift()` — пересканирование для `structure_modified`** теперь вызывает
  `scan_forms(root, include_elem_only=True)` чтобы elem-only формы попали в
  `current_elem_map` (issue #58).
- **`check_drift()` — `added/removed/modified`** вычисляются по `index_keys_bsl =
  index_keys - elem_only_keys`, исключая elem-only из BSL-based логики (issue #58).

### Fixed
- `check_drift()` / `modified`: повторная полная распаковка неизменённого `.cf`
  больше не даёт ложные `modified` — детекция переведена с `bsl_mtime` на
  `bsl_sha256` (issue #38).
- `DriftReport.modified` ранее всегда возвращал `[]` — baseline отсутствовал
  в `FormScanIndex`. Исправлено добавлением поля `bsl_mtime` (issue #18).
- scan_forms external: формы с суффиксом `.bsl` (`Form.obj.bsl`, v8unpack 1.2.11)
  не находились — режим искал только `Form.obj` без суффикса, возвращая 0 форм.
  Проверено на реальной выгрузке (13 форм: обработки + отчёты) (issue #32).
- **`_stale_keys()`: elem-only формы больше не попадают в `stale_extractions`.**
  Отсутствие `bsl_path` у elem-only форм — это дизайн, а не признак устаревшей
  экстракции. Исправлено пропуском ключей из `elem_only_keys` (issue #58).
  Проверено: 49 elem-only форм реальной конфигурации УТ 10.3 — ложный дрейф
  устранён.
- **`structure_modified` для elem-only форм**: пересечение с `disk_keys` (только
  BSL-формы) давало пустое множество — elem-only формы никогда не проверялись.
  Исправлено: кандидаты берутся напрямую из `index_elem` (issue #58).
