# catalog_resolver

Модуль `catalog_resolver` реализует best-effort резолюцию `data_path` элемента
формы через JSON-описание объекта метаданных (`Catalog.json`, `Document.json` и др.).

## Публичный API

### `ResolvedBinding`

```python
@dataclass(frozen=True)
class ResolvedBinding:
    data_path: str        # исходная строка, например "Объект.Город"
    object_type: str      # "Catalog", "Document", …
    attribute_name: str   # "Город"
    value_type: str | None
    synonym: str | None
    resolved: bool        # True — реквизит найден и декодирован
```

### `resolve_data_path`

```python
def resolve_data_path(data_path: str, object_json: Path) -> ResolvedBinding:
```

Принимает строку `data_path` (`"Объект.Реквизит"` или `"Объект.ТЧ.Реквизит"`)
и путь к JSON объекта. Возвращает `ResolvedBinding`.

Поддерживаемые форматы пути:

| Формат | Поиск |
|---|---|
| `Объект.Реквизит` | `Properties` верхнего уровня |
| `Объект.ТЧ.Реквизит` | `TabularSections` → `Properties` |

### `object_json_path`

```python
def object_json_path(form_entry: FormEntry) -> Path | None:
```

Поднимается на 2 уровня вверх от `form_entry.form_path`, ищет `.json`-файл
объекта (сначала по имени объекта, затем fallback по типу — `Catalog.json`).
Возвращает `None`, если файл не найден.

## Поведение

| Ситуация | Результат |
|---|---|
| Реквизит найден | `resolved=True`, заполнены `value_type`, `synonym` |
| `Catalog.json` отсутствует | `resolved=False`, без исключений |
| Реквизит не найден в JSON | `resolved=False` |
| Вложенный путь `Объект.ТЧ.Реквизит` | Ищет в `TabularSections` → `Properties` |
| Одиночный сегмент без точки | `resolved=False` |
| Любая другая ошибка | `resolved=False`, без исключений |

## Известные ограничения

### #85 — декодирование `data_path` — закрыто

`parse_elem_json` заполняет `data_path` из секции `data` файла `*.elem.json`:
обычные формы — через поле `prop`, управляемые — через UUID реквизита
объекта-владельца. Подробности механизма — в
[docs/elem_parser.md](elem_parser.md).

Связка `elem_parser` → `catalog_resolver` работает на реальных выгрузках:
`FormSummary.relations[kind=data]` заполняется, и `resolve_data_path`
получает подтверждённые пути вида `СправочникОбъект.Город` (обычные формы)
или `Объект.Город` (управляемые).

Элементы без привязки — надписи, группы, страницы, панели команд — это
норма, а не пробел в декодировании.

Если UUID не дал результата, управляемая форма использует только
консервативный структурный fallback: точное имя реквизита формы либо колонку
через непосредственный родительский реквизит. Вложенные узлы `props`
учитываются рекурсивно; префиксные догадки запрещены. #84, описанная ниже,
нужна для обогащения пути типом и синонимом, а не для расширения эвристик.

### Зависимость от #84 — декодирование `Catalog.json/header`

Текущий `Catalog.json` хранит реквизиты объекта в raw-секции `header`
(недекодированный формат v8unpack), а не в читаемых полях `Properties` /
`Attributes`. До реализации [#84](https://github.com/MRDK80/v8unpack-agent/issues/84)
(`decode_object_attributes`) `resolve_data_path` возвращает `resolved=False`
на реальных выгрузках — даже при корректном `data_path`.

### Итоговая картина

```text
*.elem.json
  → parse_elem_json             ← #85 ✅ реализовано, PR #86
        ↓
  data_path подтверждённый
    обычные формы    — через prop
    управляемые      — через UUID, затем точный структурный fallback
        ↓
  catalog_resolver              ← #76 ✅ реализовано, PR #83
        ↓ (требует читаемых Properties)
  decode_object_attributes      ← #84, ожидается
        ↓
  ResolvedBinding(resolved=True)
```

До завершения #84 модуль работает в режиме best-effort: `resolved=False`
на реальной выгрузке — штатный результат, а не ошибка. Входные `data_path`
при этом уже достоверны.

На контрольной реальной выгрузке структурный fallback безопасно восстановил
3292 из 4549 исходно неразрешённых Field-записей; 1257 записей оставлены без
догадок. Это диагностический результат конкретной выгрузки, а не обещание
универсального покрытия.

## Пример использования

```python
from pathlib import Path
from v8unpack_agent.catalog_resolver import resolve_data_path, object_json_path
from v8unpack_agent.scan_forms import FormEntry

form_entry: FormEntry = ...  # из scan_forms()

obj_json = object_json_path(form_entry)
if obj_json:
    binding = resolve_data_path("Объект.Город", obj_json)  # путь из parse_elem_json
    if binding.resolved:
        print(binding.value_type, binding.synonym)
    else:
        print("резолюция недоступна — ожидается #84")
```

## Связь с конвейером №3a

| Задача | Статус |
|---|---|
| [#76](https://github.com/MRDK80/v8unpack-agent/issues/76) `catalog_resolver` | ✅ реализовано, PR [#83](https://github.com/MRDK80/v8unpack-agent/pull/83) |
| [#85](https://github.com/MRDK80/v8unpack-agent/issues/85) `decode_element_data_path` | ✅ реализовано, PR [#86](https://github.com/MRDK80/v8unpack-agent/pull/86) |
| [#84](https://github.com/MRDK80/v8unpack-agent/issues/84) `decode_object_attributes` | 🔲 open |
| [#77](https://github.com/MRDK80/v8unpack-agent/issues/77) `form_context` | 🔲 open, ожидает #84 |
