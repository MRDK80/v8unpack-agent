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

### Прочее
- `elem_parser.parse_elem_json()` — единственный парсер `*.elem.json` — issue #40
- `form_summary.build_form_summary()` — семантическая выжимка формы — issue #66
- `managed_forms.discover_elem_forms()` — обнаружение elem-only форм — issue #55
- `form_router` — маршрутизация запросов к формам по типу/имени

## Не реализовано / В планах

- CLI для `check_drift` (аналогично `scan_forms --mode`)
- Детекция дрейфа по `form_summary` (семантический уровень)
- Инкрементальный baseline (обновление только изменённых форм)

## Известные ограничения

- `elem_sha256` вычисляется только при наличии `*.elem.json`; формы без него
  не участвуют в `structure_modified` (не баг, дизайн).
- Вложенность групп в `elem_parser` не реконструируется полностью —
  хэш строится по достоверной части дерева.
