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

## Источник данных

Читаемых секций `Properties` / `Attributes` в `Catalog.json` нет: реквизиты
объекта хранятся в raw-секции `header` в частично декодированном формате
v8unpack. Разбор этой секции выполняет отдельный модуль
[`object_decoder`](object_decoder.md) (#84, PR #87), который возвращает
структуру, совместимую с `resolve_data_path` без изменения его публичного API.

Имя ссылочного типа приходит из того же модуля после #88 (PR #118): резолвер
`uuid → имя типа` строится в `scan_forms` и передаётся в
`decode_object_attributes` как `type_resolver`. Публичный API
`catalog_resolver` при этом не менялся — в `value_type` просто приходит
читаемое имя вместо `Ref#<uuid>`.

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
учитываются рекурсивно; префиксные догадки запрещены.

### #84 — декодирование `Catalog.json/header` — закрыто

`decode_object_attributes` декодирует raw-секцию `header` и отдаёт имя, UUID
и тип реквизита, а также табличные части с колонками. После #84
`resolved=True` достижим на реальных выгрузках.

Прогон на production-выгрузке (6551 объект метаданных, 15888 реквизитов):

| Категория `value_type` | Кол-во | Доля |
|---|---:|---:|
| Примитивы (`String`, `Number`, `Boolean`, `Date`) | 7457 | 46.9% |
| Ссылочные (`Ref#<uuid>`) | 5226 | 32.9% |
| `None` (compact-layout, составные типы) | 3205 | 20.2% |

Нераспознанных кодов типа — 0, ошибок декодирования — 0.

### #88 — имена ссылочных типов — закрыто

Ссылочный тип приводится к имени объекта метаданных: `CatalogRef.Регионы`,
`DocumentRef.РеализацияТоваров`, `EnumRef.ЮрФизЛицо` и т. д. Глобальный индекс
`uuid → имя типа` строится в `scan_forms` во время уже существующего обхода
выгрузки и передаётся декодеру как `type_resolver`; второго обхода дерева нет.

Постановка #88 предполагала, что ссылка адресует UUID объекта из
`header[0][1][2]`. На реальных данных такой индекс дал ноль резолюций:
ссылка адресует соседние слоты того же блока идентификации (`[1]` и `[3]`).
Индексируются все валидные UUID блока `header[0][1]` — они принадлежат одному
объекту, поэтому имя типа для них одно.

Прогон на контрольной выгрузке текущей версией инструмента (15717 реквизитов):

| Метрика | без резолвера | с резолвером |
|---|---:|---:|
| Ссылочных `Ref#uuid` | 5226 | 556 |
| Разрешено в читаемые имена | 0 | 4670 |
| Изменено нессылочных записей | — | 0 |
| `data_path` изменён | — | 0 |
| Потерь и исключений | 0 | 0 |

Метрики #84 и #88 считаны разными версиями инструмента (15888 против 15717
реквизитов) и не смешиваются.

Неизвестный UUID остаётся `Ref#<uuid>` — тип не угадывается. Дополнительно
`value_type=None` остаётся у compact-layout и составных типов
(`CompositeType`) — их разбор в #84 не входил и в #88 не расширялся.

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
        ↑ (читаемые Properties)
  decode_object_attributes      ← #84 ✅ реализовано, PR #87
        ↑ (type_resolver из FormScanIndex)
  индекс ссылочных типов        ← #88 ✅ реализовано, PR #118
        ↓
  ResolvedBinding(resolved=True, value_type="String" | "CatalogRef.Регионы" | "Ref#<uuid>")
```

`resolved=False` остаётся штатным результатом для реквизитов, отсутствующих
в описании объекта, и для нераспознанного формата пути.

На контрольной реальной выгрузке структурный fallback безопасно восстановил
3292 из 4549 исходно неразрешённых Field-записей; 1257 записей оставлены без
догадок. Это диагностический результат конкретной выгрузки, а не обещание
универсального покрытия. Корректная метрика покрытия по всем элементам
данных — задача [#90](https://github.com/MRDK80/v8unpack-agent/issues/90).

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
        print(binding.value_type, binding.synonym)   # "CatalogRef.Города"
    else:
        print("реквизит не найден в описании объекта")
```

## Связь с конвейером №3a

| Задача | Статус |
|---|---|
| [#76](https://github.com/MRDK80/v8unpack-agent/issues/76) `catalog_resolver` | ✅ реализовано, PR [#83](https://github.com/MRDK80/v8unpack-agent/pull/83) |
| [#85](https://github.com/MRDK80/v8unpack-agent/issues/85) `decode_element_data_path` | ✅ реализовано, PR [#86](https://github.com/MRDK80/v8unpack-agent/pull/86) |
| [#84](https://github.com/MRDK80/v8unpack-agent/issues/84) `decode_object_attributes` | ✅ реализовано, PR [#87](https://github.com/MRDK80/v8unpack-agent/pull/87) |
| [#90](https://github.com/MRDK80/v8unpack-agent/issues/90) метрика покрытия `data_path` | ✅ реализовано, PR [#97](https://github.com/MRDK80/v8unpack-agent/pull/97) |
| [#89](https://github.com/MRDK80/v8unpack-agent/issues/89) формы с нулевой привязкой | ✅ реализовано, PR [#115](https://github.com/MRDK80/v8unpack-agent/pull/115) |
| [#88](https://github.com/MRDK80/v8unpack-agent/issues/88) `Ref#uuid` → имя объекта | ✅ реализовано, PR [#118](https://github.com/MRDK80/v8unpack-agent/pull/118) |
| [#116](https://github.com/MRDK80/v8unpack-agent/issues/116) 42 формы без блока привязки | 🔲 open |
| [#77](https://github.com/MRDK80/v8unpack-agent/issues/77) `form_context` | 🔲 open |
| [#98](https://github.com/MRDK80/v8unpack-agent/issues/98) `form_classifier`: объектные vs. сервисные формы | ✅ реализовано, PR [#99](https://github.com/MRDK80/v8unpack-agent/pull/99) |
