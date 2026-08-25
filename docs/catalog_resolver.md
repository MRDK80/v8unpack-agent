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
| `Объект.Реквизит` | `DecodeResult.data["Properties"]` |
| `Объект.ТЧ.Реквизит` | `DecodeResult.data["TabularSections"]` → `Properties` |

Сопоставление имён регистронезависимое — имена метаданных 1С такими и являются.
Пути глубже трёх сегментов не резолвятся: подтверждённые правила
интерпретации `data_path` в #148 не расширялись.

### `object_json_path`

```python
def object_json_path(form_entry: FormEntry) -> Path | None:
```

`object_json_path()` возвращает `None`, если в `FormEntry` отсутствует уровень
`ObjectName` (`object_name == ""`). В этом layout объекта-владельца нет;
функция не поднимается до корня выгрузки и не ищет там fallback JSON (#172).

Признак — структура записи, а не имя типа метаданных: проверка по
`object_type == "CommonForm"` была бы эвристикой, а абсолютная глубина пути
зависит от расположения корня выгрузки. Записи, у которых атрибут `object_name`
отсутствует или равен `None`, поведение не меняют — сравнение выполняется
строго с пустой строкой.

Поднимается на 2 уровня вверх от `form_entry.form_path`, ищет `.json`-файл
объекта (сначала по имени объекта, затем fallback по типу — `Catalog.json`).
Возвращает `None`, если файл не найден.

### `clear_object_cache`

```python
def clear_object_cache() -> None:
```

Сбрасывает кэш декодированных файлов объектов (#148). Нужен долгоживущим
процессам и тестам, которые перезаписывают файл объекта; на результат резолюции
не влияет.

## Поведение

| Ситуация | Результат |
|---|---|
| Реквизит найден | `resolved=True`, заполнены `value_type`, `synonym` |
| `Catalog.json` отсутствует | `resolved=False`, без исключений |
| Повреждённый JSON | `resolved=False`, без исключений |
| `DecodeResult.ok is False` | `resolved=False`, без исключений |
| Реквизит не найден в описании объекта | `resolved=False` |
| Вложенный путь `Объект.ТЧ.Реквизит` | Ищет в `TabularSections` → `Properties` |
| Табличная часть не найдена | `resolved=False` |
| Одиночный сегмент без точки | `resolved=False` |
| Путь глубже трёх сегментов | `resolved=False` |
| Любая другая ошибка | `resolved=False`, без исключений |

Тип, имя и синоним никогда не достраиваются по догадке.

## Источник данных

Читаемых секций `Properties` / `Attributes` в `Catalog.json` нет: реквизиты
объекта хранятся в raw-секции `header` в частично декодированном формате
v8unpack. Разбор этой секции выполняет отдельный модуль
[`object_decoder`](object_decoder.md) (#84, PR #87), который возвращает
структуру, совместимую с `resolve_data_path` без изменения его публичного API.

Начиная с #148 `resolve_data_path` **не читает файл объекта самостоятельно**:
единственная точка декодирования — `decode_object_attributes(object_json)`.
Резолвер проверяет `DecodeResult.ok` и работает только с `DecodeResult.data`.
Второго парсера raw-header в проекте нет; старые внутренние helpers
`_get_attributes_section` и `_get_tabular_attributes_section` удалены.

Как следствие, нормализованный layout (готовые списки `Properties` в корне
JSON) поддерживается ровно в той мере, в какой его принимает сам декодер.
Синтетические фикстуры тестов переведены на raw-header именно поэтому.

Имя ссылочного типа приходит из того же модуля после #88 (PR #118): резолвер
`uuid → имя типа` строится в `scan_forms` и передаётся в
`decode_object_attributes` как `type_resolver`. Публичный API
`catalog_resolver` при этом не менялся — в `value_type` просто приходит
читаемое имя вместо `Ref#<uuid>`.

## Кэширование

`form_context._resolve_relations()` вызывает `resolve_data_path()` отдельно для
каждой data-связи, то есть один и тот же файл объекта декодируется многократно.
Внутри модуля используется ограниченный кэш с ключом
`(path, stat.st_mtime_ns, stat.st_size)` и лимитом 32 записи.

Безусловный `lru_cache` только по `Path` неприменим: файл может измениться
в долгоживущем процессе или внутри теста. Если файл недоступен, результат
не кэшируется вовсе. Кэш не влияет на публичный контракт и может быть удалён
без изменения поведения; для тестов доступен `clear_object_cache()`.

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

### #148 — raw-header в `resolve_data_path` — закрыто

До #148 `resolve_data_path` читала файл объекта самостоятельно через
`json.loads` и искала нормализованные ключи `Properties` / `Attributes` в корне
JSON. Production-выгрузка таких ключей не содержит, поэтому функция была
неработоспособна на реальных данных независимо от корректности `data_path`.

Прогон на боевой выгрузке, `Catalog/Номенклатура/CatalogForm/ФормаЭлемента`:

| Метрика | до #148 | после #148 |
|---|---:|---:|
| `resolved_relations`, всего | 65 | 65 |
| `resolved=True` | 0 | 40 |
| `resolved=False` | 65 | 25 |

Обезличенные примеры: `value_type='String', synonym='Полное наименование'`
и `value_type='String', synonym='Артикул'`.

Оставшиеся 25 записей — отдельные категории путей (стандартные реквизиты
платформы, реквизиты самой формы, `Ref#uuid` с неизвестным UUID), которые
разбираются в #147 и #143 и в scope #148 не входили.

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
        ↑ (единственный источник реквизитов, #148)
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
| [#148](https://github.com/MRDK80/v8unpack-agent/issues/148) raw-header в `resolve_data_path` | ✅ реализовано, PR [#159](https://github.com/MRDK80/v8unpack-agent/pull/159) |
| [#116](https://github.com/MRDK80/v8unpack-agent/issues/116) 42 формы без блока привязки | 🔲 open |
| [#77](https://github.com/MRDK80/v8unpack-agent/issues/77) `form_context` | 🔲 open |
| [#98](https://github.com/MRDK80/v8unpack-agent/issues/98) `form_classifier`: объектные vs. сервисные формы | ✅ реализовано, PR [#99](https://github.com/MRDK80/v8unpack-agent/pull/99) |
