# Структура распакованных управляемых форм (Managed)

Подтверждено на реальных и синтетических данных: issue #55 (июль 2026).

v8unpack 1.2.11 не отдаёт `Form.xml` для управляемых форм. Реальный носитель
структуры — `*.elem.json`, расположенный в каталоге формы.

## Layout-варианты путей

Discovery устойчив к глубине: контейнер определяется по суффиксу `Form`
родительского каталога формы, независимо от числа уровней.

### 4-уровневый layout (Catalog, Document и др.)

    <root>/<object_type>/<object_name>/<ContainerForm>/<form_name>/
    ├── <form_name>.elem.json    # структура управляемой формы (обязательный)
    ├── <form_name>.10.json      # метаданные формы v8unpack (опциональный)
    └── <form_name>.obj.10.bsl  # модуль формы (опциональный)

Примеры:

    Catalog/Банки/CatalogForm/ФормаЭлементаУправляемая/CatalogForm.elem.json
    Document/АвансовыйОтчет/DocumentForm/ФормаДокументаУправляемая/DocumentForm.elem.json

### 3-уровневый layout (CommonForm)

    <root>/CommonForm/<form_name>/
    └── CommonForm.elem.json

Пример:

    CommonForm/ФормаВыбораПериода/CommonForm.elem.json

### 3-уровневый layout (внешние объекты — EPF / ERF)

    <root>/<object_name>/Form/<form_name>/
    └── Form.elem.json

    <root>/<object_name>/ReportForm/<form_name>/
    └── ReportForm.elem.json

Примеры:

    ВнешняяОбработкаУпр/Form/ФормаВнешняяУправляемая/Form.elem.json
    ВнешнийОтчетУправляемый/ReportForm/ФормаОтчетаУправляемая/ReportForm.elem.json

## Поддерживаемые контейнеры форм

| Суффикс каталога | Назначение |
|------------------|------------|
| `CatalogForm`    | Формы справочников |
| `DocumentForm`   | Формы документов |
| `Form`           | Формы обработок / внешних обработок |
| `ReportForm`     | Формы отчётов / внешних отчётов |
| `CommonForm`     | Общие формы конфигурации |

Поддерживается любой каталог с суффиксом `Form` (например `TaskForm`,
`BusinessProcessForm` и т.д.).

## Определение управляемой формы

Форма считается **управляемой**, если в её каталоге присутствует хотя бы один
файл `*.elem.json`. Разбор содержимого XML для discovery не используется и не
требуется. `Form.xml` находится вне pipeline v8unpack-agent (исторически
присутствовал в старых версиях v8unpack, но не является источником структуры
управляемой формы).

## Артефакты формы

| Артефакт | Обязательный | Описание |
|----------|--------------|----------|
| `<form_name>.elem.json` | ✅ | Структура управляемой формы (дерево элементов, свойства, команды, параметры) |
| `<form_name>.10.json`   | ❌ | Метаданные формы, версия 10 |
| `<form_name>.obj.10.bsl`| ❌ | Модуль формы (BSL-код обработчиков) |

## Отличия от классических (ordinary) форм

| Аспект | Классическая форма | Управляемая форма |
|--------|-------------------|-------------------|
| Основной артефакт | `Form.elem` / `<Container>.obj.bsl` | `*.elem.json` |
| `Form.xml` | присутствует (legacy) | отсутствует в pipeline |
| bsl модуля формы | `<Container>.obj.bsl` | `*.obj.10.bsl` (опциональный) |
| JSON-метаданные | `<Container>.json` | `*.10.json` (опциональный) |

## Маппинг в ManagedFormEntry

- `elem_json_path` → путь к `*.elem.json` (относительный от корня распаковки)
- `aux_json_path` → `*.10.json` (или `None` если файл отсутствует)
- `bsl_path` → `*.obj.10.bsl` (или `None` если файл отсутствует)

## Использование

```python
from pathlib import Path
from v8unpack_agent.managed_forms import discover_managed_forms

forms = discover_managed_forms(Path("path/to/unpacked/cf"))
for form in forms:
    print(form.elem_json_path)
```

## Открытые вопросы

- Запись управляемых форм в общий реестр — issue #57.
- Расчёт дрейфа управляемых форм — issue #58.
- Классификатор `ordinary/managed` как отдельный шаг — issue #56 (исключён из pipeline).
