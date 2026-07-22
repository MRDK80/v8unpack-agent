"""Тесты единого реестра форм с elem_json_path (issue #57).

Покрывает все Acceptance Criteria:
- AC1: scan_forms возвращает ordinary + external + elem-формы в одном FormScanIndex.
- AC2: у elem-форм заполнен elem_json_path (relative-to-r