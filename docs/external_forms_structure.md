# Структура распакованных внешних обработок и отчётов (External)

Подтверждено на реальных данных: обработки — 02.07.2026 (Windows Server);
отчёты и суффикс `.obj.bsl` (v8unpack 1.2.11) — 05.07.2026 (issue #32).

## Паттерн путей

Внешняя обработка (`.epf`) — контейнер форм `Form/`:

    External/<имя обработки>/
    ├── ExternalDataProcessor.obj.bsl  # модуль объекта (не форма)
    └── Form/
        └── <ИмяФормы>/
            ├── Form.obj.bsl           # bsl формы (v8unpack 1.2.11)
            ├── Form.json              # метаданные формы
            ├── Form.elem              # структура формы (элементы)
            └── Form.id

Внешний отчёт (`.erf`) — контейнер форм `ReportForm/`:

    External/<имя отчёта>/
    └── ReportForm/
        └── <ИмяФормы>/
            ├── ReportForm.obj.bsl     # bsl формы (v8unpack 1.2.11)
            ├── Form.json              # метаданные формы
            ├── Form.elem              # структура формы (элементы)
            └── Form.id

Имя bsl-файла формы соответствует контейнеру: `Form.obj.bsl` для обработки,
`ReportForm.obj.bsl` для отчёта. Модуль объекта отчёта может отсутствовать.

## Совместимость версий

| Артефакт | v8unpack 1.2.11 | legacy (старые выгрузки) |
|----------|-----------------|--------------------------|
| bsl формы обработки | `Form.obj.bsl` | `Form.obj` |
| bsl формы отчёта | `ReportForm.obj.bsl` | `ReportForm.obj` |

Сканер берёт первый существующий кандидат, приоритет у `.bsl` (issue #32).
Старый вариант без суффикса поддержан для обратной совместимости.

## Отличия от конфигурации

| Аспект | Конфигурация | External |
|--------|-------------|----------|
| bsl формы | `<Container>.obj.bsl` | `<Container>.obj.bsl` / legacy `<Container>.obj` |
| верхний уровень | `object_type` | имя обработки/отчёта |
| контейнер форм | `<...>Form` | `Form/` (обработка) либо `ReportForm/` (отчёт) |

## Определение типа объекта

- контейнер `ReportForm/` ⇒ `object_type = "ExternalReport"` (по контейнеру,
  приоритет над модулем объекта);
- контейнер `Form/` ⇒ по имени модуля объекта
  (`ExternalDataProcessor.obj.bsl` / `ExternalReport.obj.bsl`); при отсутствии
  модуля — fallback `ExternalDataProcessor`.

## Маппинг в FormEntry

- `object_type` = `"ExternalDataProcessor"` либо `"ExternalReport"`
  (не пересекается с типами конфигурации)
- `object_name` = `<имя обработки/отчёта>`
- `container_name` = `"Form"` либо `"ReportForm"`, `form_name` = `<имя папки формы>`
- `bsl_path` → `<Container>.obj.bsl` (или legacy `<Container>.obj`)
- `form_elem_path` → `Form.elem` (или `None`)

## Использование

    python -m v8unpack_agent.scan_forms <root> --mode external --save

## Открытые вопросы

- Хранение модуля объекта (`*.obj.bsl` уровня обработки/отчёта) — вне scope #25/#32.
- `Template/` (макеты) — вне scope happy path #25.
