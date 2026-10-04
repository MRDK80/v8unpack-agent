# Универсальная модель BSL-модулей

Модуль `v8unpack_agent.modules` задаёт неизменяемые типизированные
`ModuleEntry` и `ModuleIndex` — общий результат для специализированных
сканеров BSL-модулей эпика #201 (#204–#207). Контракт добавлен в issue #203 и
основан на результатах исследования #202:
[`docs/research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md).

Модель не зависит от `FormContext` и конкретного файлового layout, не
обращается к файловой системе и не хранит BSL-текст. Сканирование конкретных
видов модулей, runner, CLI, отчёты, RAG и разбор BSL в модель не входят.

## Импорт

```python
from v8unpack_agent.modules import (
    MODULE_INDEX_SCHEMA,
    MODULE_KINDS,
    MODULE_READ_STATUSES,
    OWNER_KINDS,
    ModuleEntry,
    ModuleIndex,
    ModuleKind,
    ModuleReadStatus,
    OwnerKind,
    classify_bsl_bytes,
    module_entry_from_common_module,
    validate_relative_module_path,
)
```

Корневой `v8unpack_agent.__init__` не расширяется.

## Закрытые наборы значений

Наборы соответствуют кандидатам #202 и проверяются при создании записи;
неизвестное значение даёт `ValueError`.

`ModuleKind`: `command`, `common_module`, `external_connection`, `form`,
`managed_application`, `manager`, `object`, `ordinary_application`,
`record_set`, `service`, `session`, `value_manager`.

`OwnerKind`: `common_command`, `common_form`, `common_module`,
`configuration`, `extension`, `external_data_processor`, `external_report`,
`metadata_object`, `metadata_object_command`, `metadata_object_form`.

`ModuleReadStatus`:

| Значение | Смысл | Соответствие в JSON #202 |
|---|---|---|
| `ok` | непустой текст UTF-8 | `nonempty` |
| `empty` | 0 байт или только UTF-8 BOM | `empty` |
| `whitespace_only` | после BOM только пробельные символы | `whitespace_only` |
| `missing` | файла по ожидаемому пути нет | `missing_file` |
| `read_error` | невалидный UTF-8 или `OSError` | `read_error` |

`missing` означает «файла нет», а не «модуля нет»: #202 показал, что
v8unpack не записывает часть пустых модулей. Запись со статусом `missing`
хранит ожидаемый относительный путь и не может иметь `size_bytes` или
`sha256`.

`classify_bsl_bytes(data)` — чистая функция без обращения к диску: она
различает `ok`, `empty`, `whitespace_only` и `read_error` по уже прочитанным
байтам. Статус `missing` определяет вызывающий сканер.

`CommonModuleContext` из `common_modules` сохраняет прежнюю семантику
`ok | empty | missing | read_error`, где текст из одних пробелов считается
`ok`. Новые сканеры обязаны различать `whitespace_only`.

## ModuleEntry

```python
@dataclass(frozen=True)
class ModuleEntry:
    module_kind: ModuleKind
    owner_kind: OwnerKind
    metadata_type: str | None
    owner_name: str | None
    relative_path: str
    read_status: ModuleReadStatus
    size_bytes: int | None = None
    sha256: str | None = None
```

- `metadata_type` — тип владельца как ASCII-идентификатор (`Catalog`,
  `InformationRegister`, `Enum`, `Sequences`, `CommonModule`,
  `ConfigurationExtension`, ...). Он обязателен для всех владельцев, кроме
  `configuration`. Суффикс файла `obj` неоднозначен: у справочника это
  `object`, у регистра сведений — `record_set`, у константы —
  `value_manager`, у перечисления — `manager`. Поэтому `module_kind` задаёт
  сканер по паре «тип владельца + суффикс», а модель его не выводит.
- `owner_name` — имя владельца: идентификаторы 1С, разделённые точкой. Для
  формы и команды объекта это `<Объект>.<Форма>` / `<Объект>.<Команда>`.
  Для `configuration` оба поля — `None`.
- `size_bytes` — неотрицательный `int` (не `bool`) или `None`; `sha256` —
  64 строчных шестнадцатеричных символа или `None`.

Модель проверяет формат полей, но не проверяет совместимость пар
`owner_kind`/`metadata_type`/`module_kind`. Соответствия задают сканеры по
доказанным строкам #202.

### Стабильный идентификатор

`module_id` вычисляется из полей и не зависит от пути и ОС:

```text
{owner_kind}:{metadata_type или ""}:{owner_name или ""}:{module_kind}
```

Примеры: `configuration:::session`,
`metadata_object:InformationRegister:Alpha:record_set`,
`metadata_object_form:Catalog:Alpha.ItemForm:form`.

Идентификаторы 1С не содержат `:` и `.` внутри имени, поэтому разбор
однозначен. Имена 1С нечувствительны к регистру, поэтому уникальность
проверяется через `casefold()`.

### Граница пути

`relative_path` — строка в POSIX-форме относительно корня выгрузки. Правила
проверяются одновременно по семантике POSIX и Windows и не зависят от ОС
запуска. Функция `validate_relative_module_path()` отклоняет:

- не-строку (`TypeError`), пустую строку и управляющие символы;
- обратную косую черту в любом месте: разделитель только `/`;
- абсолютный путь POSIX (`/…`, `//…`) и Windows (`C:/…`, диск без корня
  `C:…`, UNC, путь от корня диска);
- пустые сегменты, `.` и `..`, завершающий `/`;
- `:` в сегменте (диск, NTFS-потоки);
- сегмент, оканчивающийся пробелом или точкой;
- зарезервированные имена устройств Windows (`CON`, `PRN`, `AUX`, `NUL`,
  `COM1`–`COM9`, `LPT1`–`LPT9`) в основе сегмента без учёта регистра;
- имя файла без суффикса `.bsl` в нижнем регистре.

Путь не нормализуется: допустимое значение хранится без изменений.
Symlink и реальное существование файла модель не проверяет, потому что не
обращается к диску; это задача сканера.

## ModuleIndex

```python
@dataclass(frozen=True)
class ModuleIndex:
    entries: tuple[ModuleEntry, ...] = ()
```

- `ModuleIndex.from_entries(iterable)` копирует вход в `tuple`.
- Допускаются только `ModuleEntry` (`TypeError`).
- Дубликат `module_id` или `relative_path` без учёта регистра даёт
  `ValueError`.
- Записи сортируются по `(relative_path.casefold(), relative_path)`.
  Порядок не зависит от ОС, `os.sep` и порядка обхода файловой системы.
- `total`, `len()`, итерация, `get(module_id)` без учёта регистра.
- `filter(module_kind=, owner_kind=, metadata_type=, read_status=)`
  возвращает новый `ModuleIndex`; неизвестное значение закрытого набора даёт
  `ValueError`.

Индекс строго read-only: он не читает и не пишет файлы и не вызывает
`scan_forms()`, который записывает JSON в каталоги форм.

## Сериализация

`to_dict()` / `to_json()` / `from_dict()`:

```json
{
  "entries": [
    {
      "metadata_type": null,
      "module_id": "configuration:::session",
      "module_kind": "session",
      "owner_kind": "configuration",
      "owner_name": null,
      "read_status": "ok",
      "relative_path": "Configuration.seance.bsl",
      "sha256": null,
      "size_bytes": null
    }
  ],
  "schema": "module_index/1",
  "total": 1
}
```

`to_json()` детерминирован: `sort_keys=True`, `indent=2`,
`ensure_ascii=False`, перевод строки LF в конце. BSL-текста в модели нет,
поэтому он не попадает в сериализацию. Абсолютных путей в модели быть не
может. Имена владельцев сериализуются как есть: обезличенные отчёты должны
агрегировать данные на своём уровне.

`from_dict()` требует схему `module_index/1`, точный набор ключей записи,
совпадение `module_id` с полями и `total` с числом записей.

## Совместимость с CommonModuleEntry

`CommonModuleEntry`, `CommonModuleIndex`, `CommonModuleContext` и
`scan_common_modules()` не меняются. Для перехода используется адаптер:

```python
entry = module_entry_from_common_module(common_entry, "ok")
```

Он даёт `module_kind="common_module"`, `owner_kind="common_module"`,
`metadata_type="CommonModule"`, `owner_name=common_entry.name` и
`relative_path=common_entry.bsl_path.as_posix()`. Статус чтения передаёт
вызывающий код. Небезопасный путь отклоняется правилами модели. API форм
(`FormEntry`, `FormScanIndex`, `scan_forms`, `FormContext`) не меняется.

## Регистр имён в выгрузке

Сканеры ищут каталоги типов, контейнеры команд и файлы модулей по точным
каноничным именам (`Catalog`, `Catalog.obj.bsl`, `Configuration.app.bsl`).
Сравнение побайтовое, по именам из `os.scandir()`, и одинаково на Linux и
Windows: каталог `catalog` вместо `Catalog` не находится ни на одной ОС, а
файл с неточным регистром даёт `missing`. Имена владельцев берутся с диска как
есть. Нормализация Юникода не выполняется. Точное сопоставление реализовано во
внутреннем модуле `v8unpack_agent._exact_names`.

## LLM-проекция модуля (#345)

`v8unpack_agent.module_projection` превращает запись `ModuleEntry` и текст
модуля в детерминированный текст для LLM. Функция чистая: файлы не читаются,
LLM-провайдер не вызывается, BSL-текст передаёт вызывающий код. Имена не
экспортируются из корневого пакета и не подключены к CLI и post-run report.

```python
to_llm_module_fragment(entry, bsl_text, *, max_chars=-1) -> ModuleProjection
```

`ModuleProjection` — неизменяемый результат:

| Поле | Значение |
| --- | --- |
| `status` | значение `entry.read_status` |
| `text` | готовый текст; пуст, если текста нет или он не помещается в бюджет |
| `truncated` | `True`, если для `ok` текст усечён или пуст из-за бюджета |
| `original_chars` | длина тела после нормализации переводов строк; 0 без текста |

### Статусы без текста

Для `empty`, `whitespace_only`, `missing` и `read_error` результат —
`text=""`, `truncated=False`, `original_chars=0`; переданный `bsl_text`
игнорируется, статус сохраняется и различим. `missing` и `read_error` —
ограничение доступных данных, а не доказательство отсутствия логики в
конфигурации.

### Формат для `ok`

Заголовок и тело. Строки `metadata_type` и `owner` пропускаются, если у
владельца их нет (модули уровня конфигурации). В заголовок попадают только
вид модуля, вид и имя владельца и относительный POSIX-путь из `ModuleEntry`;
абсолютные пути и тексты исключений не включаются.

```text
MODULE
kind: object
owner_kind: metadata_object
metadata_type: Catalog
owner: Items
path: Catalog/Items/Ext/ObjectModule.bsl
BSL
<тело модуля>
```

### Бюджет и усечение

`max_chars=-1` отключает лимит. При `max_chars >= 0` всегда
`len(text) <= max_chars`. Если заголовок и тело не помещаются, тело
отрезается с конца, к тексту добавляется маркер
`[TRUNCATED: <включено> of <всего> chars]` (на новой строке), `truncated`
равен `True`; маркер входит в бюджет. Если в бюджет не помещаются заголовок
и маркер, результат — `text=""` и `truncated=True` (в том числе при
`max_chars=0`). Длина считается в символах Юникода (`len`), не в байтах.

### Детерминизм

До подсчёта длины ведущий BOM удаляется, а CRLF и CR заменяются на LF;
остальной текст не меняется. Один вход даёт один и тот же текст на Linux и
Windows. Некорректные аргументы: `max_chars` не `int` (включая `bool`) —
`TypeError`, `max_chars < -1` — `ValueError`, `bsl_text=None` при `ok` —
`ValueError`, не `ModuleEntry` — `TypeError`. BSL-текст — данные: он не
санитизируется и не проверяется на обезличенность.

## Итоговая матрица покрытия (#209)

Виды модулей, сканеры, тесты, статусы отчёта, результаты замера на трёх
выгрузках и явный остаток необработанных файлов сведены в
[`bsl_coverage_matrix.md`](bsl_coverage_matrix.md).

## Модули объекта внешних обработок и отчётов (#351)

`scan_external_object_modules(root)` из
`v8unpack_agent.external_object_modules` возвращает записи
`module_kind="object"` с `owner_kind` `external_data_processor`
(`metadata_type="ExternalDataProcessor"`) или `external_report`
(`metadata_type="ExternalReport"`). Артефакт — подкаталог корня (или
`External/`) с файлом `ExternalDataProcessor.json`; модуль объекта —
`<артефакт>/ExternalDataProcessor.obj.bsl` у обоих видов. `owner_name` —
поле `name` файла метаданных, а не имя каталога. Отчёт определяется по
контейнеру `ReportForm`, без него — по суффиксу каталога `.erf` / `.epf`.
Если имя или вид установить нельзя, а также при совпадении `module_id`
двух артефактов запись не создаётся. Отсутствующий файл модуля даёт
`missing`; в #351 доказано, что так распаковываются артефакты с пустым
модулем объекта. Runner вызывает сканер только при `mode="external"`.

## Нерешённое в #202

Модель не фиксирует спорные соответствия как доказанные:

- семантика `Sequences/{Name}/Sequences.obj.bsl`;
- `obj` у `ChartOfAccounts`, `ChartOfCalculationTypes`, `AccountingRegister`
  (сверено только с upstream `ext_code`, не с Конфигуратором);
- 1 176 модулей форм без пары у Конфигуратора (связь с #150).

Закрытые наборы от этих случаев не зависят. Сканер, который включает такие
строки, должен ссылаться на их статус в #202 и не повышать его до доказанного.
