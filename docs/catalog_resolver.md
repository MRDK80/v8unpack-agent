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

### Зависимость от #85 — декодирование `data_path` из `CatalogForm.elem.json`

`elem_parser` извлекает имена элементов из секции `tree`, но **не декодирует**
точные пути к данным из `CatalogForm.elem.json/data[*].raw`.
В результате `FormSummary.relations[kind=data]` на реальной выгрузке остаётся
пустым — `resolve_data_path` нечего резолвить.

До реализации [#85](https://github.com/MRDK80/v8unpack-agent/issues/85)
(`decode_element_data_path`) связка `elem_parser` → `catalog_resolver` работает
только на синтетических или вручную заданных `data_path`.

Поля `Город` и `Телефоны` в диагностических примерах — smoke-примеры одной
конкретной формы, а не подтверждение общей работоспособности на реальных выгрузках.
Путь `Объект.<имя элемента>` **не выводится автоматически** без подтверждения
из сериализованных данных.

### Зависимость от #84 — декодирование `Catalog.json/header`

Текущий `Catalog.json` хранит реквизиты объекта в raw-секции `header`
(недекодированный формат v8unpack), а не в читаемых полях `Properties` /
`Attributes`. До реализации [#84](https://github.com/MRDK80/v8unpack-agent/issues/84)
(`decode_object_attributes`) `resolve_data_path` возвращает `resolved=False`
на реальных выгрузках — даже при корректном `data_path`.

### Итоговая картина

```text
CatalogForm.elem.json
  → elem_parser (tree)          ← имена элементов без data_path
  → decode_element_data_path    ← #85, ожидается
        ↓
  data_path подтверждённый
        ↓
  catalog_resolver              ← #76 ✅ реализовано
        ↓ (требует читаемых Properties)
  decode_object_attributes      ← #84, ожидается
        ↓
  ResolvedBinding(resolved=True)
```

До завершения #85 и #84 модуль работает в режиме best-effort:
`resolved=False` — не ошибка, а штатный результат на реальной выгрузке.

## Пример использования

```python
from pathlib import Path
from v8unpack_agent.catalog_resolver import resolve_data_path, object_json_path
from v8unpack_agent.scan_forms import FormEntry

form_entry: FormEntry = ...  # из scan_forms()

obj_json = object_json_path(form_entry)
if obj_json:
    binding = resolve_data_path("Объект.Город", obj_json)
    if binding.resolved:
        print(binding.value_type, binding.synonym)
    else:
        print("резолюция недоступна — ожидается #84/#85")
```

## Связь с конвейером №3a

| Задача | Статус |
|---|---|
| [#76](https://github.com/MRDK80/v8unpack-agent/issues/76) `catalog_resolver` | ✅ реализовано, PR [#83](https://github.com/MRDK80/v8unpack-agent/pull/83) |
| [#85](https://github.com/MRDK80/v8unpack-agent/issues/85) `decode_element_data_path` | 🔲 open |
| [#84](https://github.com/MRDK80/v8unpack-agent/issues/84) `decode_object_attributes` | 🔲 open |
| [#77](https://github.com/MRDK80/v8unpack-agent/issues/77) `form_context` | 🔲 open, ожидает #85 и #84 |
