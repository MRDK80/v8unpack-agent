# Структура распакованных внешних обработок (External)

Подтверждено на реальных данных 02.07.2026 (Windows Server).

## Паттерн путей

    External/<имя обработки>/
    ├── ExternalDataProcessor.json
    ├── ExternalDataProcessor.obj      # модуль объекта (не форма)
    └── Form/
        └── <ИмяФормы>/
            ├── Form.obj               # bsl формы, БЕЗ суффикса .bsl
            ├── Form.json              # метаданные формы
            ├── Form.elem              # структура формы (элементы)
            └── Form.id

## Отличия от конфигурации

| Аспект | Конфигурация | External |
|--------|-------------|----------|
| bsl формы | `<Container>.obj.bsl` | `Form.obj` |
| верхний уровень | `object_type` | имя обработки |
| контейнер форм | `<...>Form` | фиксированный `Form/` |

## Маппинг в FormEntry

- `object_type = "ExternalDataProcessor"` (не пересекается с типами конфигурации)
- `object_name = <имя обработки>`
- `container_name = "Form"`, `form_name = <имя папки формы>`
- `form_elem_path` → `Form.elem` (или `None`)

## Использование

    python -m v8unpack_agent.scan_forms <root> --mode external --save

## Открытые вопросы

- Хранение модуля объекта `ExternalDataProcessor.obj` — вне scope #25.
- `Template/` (макеты) — вне scope happy path #25.