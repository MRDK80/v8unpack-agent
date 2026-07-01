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

### Changed
- `FormScanIndex.to_dict()` сериализует `bsl_mtime` в JSON.
- `drift_checker._index_snapshot()` читает `bsl_mtime` из JSON-записи индекса
  вместо прямого обращения к диску через `Path(bsl).stat().st_mtime`.

### Fixed
- `DriftReport.modified` ранее всегда возвращал `[]` — baseline отсутствовал
  в `FormScanIndex`. Исправлено добавлением поля `bsl_mtime` (issue #18).
