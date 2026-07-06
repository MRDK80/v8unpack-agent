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

### Fixed
- `check_drift()` / `modified`: повторная полная распаковка неизменённого `.cf`
  больше не даёт ложные `modified` — детекция переведена с `bsl_mtime` на
  `bsl_sha256` (issue #38).
- `DriftReport.modified` ранее всегда возвращал `[]` — baseline отсутствовал
  в `FormScanIndex`. Исправлено добавлением поля `bsl_mtime` (issue #18).
- scan_forms external: формы с суффиксом `.bsl` (`Form.obj.bsl`, v8unpack 1.2.11)
  не находились — режим искал только `Form.obj` без суффикса, возвращая 0 форм.
  Проверено на реальной выгрузке (13 форм: обработки + отчёты) (issue #32).
