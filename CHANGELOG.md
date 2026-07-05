# Changelog

Все значимые изменения фиксируются здесь.
Формат следует [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added
- `FormEntry.bsl_mtime: float` — поле mtime файла `.obj.bsl` на момент сканирования;
  используется как baseline для детекции изменённых форм (`drift_checker`).
- `FormScanIndex.load()` — загрузка индекса из JSON с обратной совместимостью:
  старые записи без `bsl_mtime` получают `0.0`, форма не попадает в `modified`.
- `DriftReport.modified` теперь **работает**: возвращает ключи форм, чей `.obj.bsl`
  изменился после записи baseline в `FormScanIndex` (issue #18).
- scan_forms: режим `--mode external` для распакованных внешних обработок
  (`External/<имя>/Form/<форма>/Form.obj`); поле `form_elem_path` в FormEntry (#25)
- scan_forms external: поддержка контейнера `ReportForm/` для внешних отчётов;
  `object_type="ExternalReport"` определяется по контейнеру `ReportForm` (#32)

### Changed
- `FormScanIndex.to_dict()` сериализует `bsl_mtime` в JSON.
- `drift_checker._index_snapshot()` читает `bsl_mtime` из JSON-записи индекса
  вместо прямого обращения к диску через `Path(bsl).stat().st_mtime`.
- scan_forms external: bsl-файл формы ищется по кандидатам `<Container>.obj.bsl`
  (v8unpack 1.2.11) → `<Container>.obj` (legacy), приоритет у `.bsl`; типизация
  для контейнера `Form/` — по имени модуля объекта с fallback
  `ExternalDataProcessor` (#32)

### Fixed
- `DriftReport.modified` ранее всегда возвращал `[]` — baseline отсутствовал
  в `FormScanIndex`. Исправлено добавлением поля `bsl_mtime` (issue #18).
- scan_forms external: формы с суффиксом `.bsl` (`Form.obj.bsl`, v8unpack 1.2.11)
  не находились — режим искал только `Form.obj` без суффикса, возвращая 0 форм.
  Проверено на реальной выгрузке (13 форм: обработки + отчёты) (issue #32).
