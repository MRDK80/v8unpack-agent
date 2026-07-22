# Структура распакованных внешних обработок и отчётов (External)

Подтверждено на реальных данных: обработки — 02.07.2026 (Windows Server);
отчёты и суффикс `.obj.bsl` (v8unpack 1.2.11) — 05.07.2026 (issue #32).
Elem-only внешние формы (без кода модуля формы) — 22.07.2026 (issue #57).

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
| `*.elem.json` | есть (конфиг, mode=config) | есть (`Form.elem.json` / `ReportForm.elem.json`) |

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
- `bsl_sha256` → SHA-256 содержимого `bsl_path`; `None` если файл отсутствует
- `form_elem_path` → `Form.elem` / `ReportForm.elem` (или `None`)
- `elem_sha256` → SHA-256 нормализованного `form_elements_index`; `None` если
  `.elem.json` отсутствует или недоступен (best-effort)
- `elem_json_path` → `*.elem.json` в каталоге формы, относительный путь
  (issue #57); заполнен, если файл присутствует, иначе `None`

## Внешние формы без кода (elem-only, issue #57)

Внешний объект может содержать управляемую форму **без собственного кода
модуля** — тогда v8unpack не создаёт `Form.obj.bsl` / `ReportForm.obj.bsl`,
но `*.elem.json` присутствует всегда. Такая форма не проходит основной
external-обход (нет обязательного bsl) и подбирается elem-only веткой
`scan_forms` при `include_elem_only=True` (по умолчанию).

Пример layout внешнего отчёта без кода:

    External/<имя отчёта>.erf/
    └── ReportForm/
        └── <ИмяФормы>/
            └── ReportForm.elem.json   # структура формы (кода .obj.bsl нет)

Метаданные восстанавливаются из relative-пути
`<object>.(epf|erf)/(Form|ReportForm)/<form>`:

| Расширение / контейнер | `object_type` | `object_name` | `container_name` |
|------------------------|---------------|---------------|------------------|
| `.erf` / `ReportForm` | `ExternalReport` | `<имя>.erf` | `ReportForm` |
| `.epf` / `Form` | `ExternalDataProcessor` | `<имя>.epf` | `Form` |

Для elem-only внешних форм: `bsl_sha256 = None`, `bsl_mtime = 0.0`,
`elem_json_path` заполнен, `warnings = ["elem-only: no .obj.bsl found"]`.

> **Фикс метаданных (issue #57).** До фикса elem-only ветка разбирала
> external-путь по правилам config-layout (`<type>/<object>/<container>/<form>`),
> из-за чего внешний управляемый отчёт без кода получал искажённые метаданные:
> пустые `object_type` / `object_name` и `form_name`, совпадающий с именем
> контейнера. Теперь при `mode="external"` путь
> `<object>.(epf|erf)/(Form|ReportForm)/<form>` разбирается корректно, а
> `object_type` выводится по контейнеру (`ReportForm` ⇒ `ExternalReport`) и
> расширению объекта (`.erf` ⇒ `ExternalReport`, иначе `ExternalDataProcessor`).
> Регрессионный тест —
> `test_external_elem_only_report_without_bsl_has_external_metadata`.

## Поля drift-детекции (FormEntry)

| Поле | Тип | Назначение |
|------|-----|------------|
| `bsl_sha256` | `Optional[str]` | Основной критерий `modified` (код формы, issue #38) |
| `bsl_mtime` | `float` | Legacy fallback для старых индексов без `bsl_sha256` |
| `elem_sha256` | `Optional[str]` | Критерий `structure_modified` (разметка формы, issue #40) |
| `elem_json_path` | `Optional[str]` | Путь к источнику структуры формы (issue #57) |

## Использование

    python -m v8unpack_agent.scan_forms <root> --mode external --save

Отключить подбор elem-only внешних форм (только формы с кодом):

    python -m v8unpack_agent.scan_forms <root> --mode external --no-elem-only

## Открытые вопросы

- Хранение модуля объекта (`*.obj.bsl` уровня обработки/отчёта) — вне scope #25/#32.
- `Template/` (макеты) — вне scope happy path #25.
