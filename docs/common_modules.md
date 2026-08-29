# Общие модули

Модуль `v8unpack_agent.common_modules` обнаруживает и читает BSL-текст
общих модулей конфигурации 1С. Это отдельный BSL-only pipeline, не связанный
с формами и `FormContext`.

Реализация добавлена как pilot расширения охвата типов метаданных в issue #151.

## Доказанная раскладка

Раскладка проверена на трёх независимых выгрузках:

```text
{root}/CommonModule/{ObjectName}/CommonModule.obj.bsl
{root}/CommonModule/{ObjectName}/CommonModule.json
{root}/CommonModule/{ObjectName}/CommonModule.id.json
```

Scanner использует только каталог объекта и `CommonModule.obj.bsl`.
`CommonModule.json` и `CommonModule.id.json` не читаются: object metadata
не входит в минимальный BSL-only pilot.

## Публичный API

Символы импортируются непосредственно из модуля:

```python
from v8unpack_agent.common_modules import (
    CommonModuleContext,
    CommonModuleEntry,
    CommonModuleIndex,
    CommonModuleReadStatus,
    build_common_module_context,
    scan_common_modules,
)
```

Корневой `v8unpack_agent.__init__` не расширяется.

### scan_common_modules

```python
scan_common_modules(root: Path) -> CommonModuleIndex
```

Каждая непосредственная дочерняя директория `{root}/CommonModule` становится
`CommonModuleEntry`. Поле `bsl_path` содержит относительный путь:

```text
CommonModule/{ObjectName}/CommonModule.obj.bsl
```

Объект попадает в индекс даже при отсутствии BSL-файла. Посторонние файлы
в контейнере и другие виды метаданных не индексируются.

Результат сортируется по относительному POSIX-пути без учёта регистра и с
детерминированным tie-break по исходной строке.

Поведение корня:

| Состояние | Результат |
|---|---|
| export root существует, `CommonModule` отсутствует | пустой индекс |
| export root отсутствует | `FileNotFoundError` |
| export root не является каталогом | `NotADirectoryError` |
| `CommonModule` существует, но не является каталогом | `NotADirectoryError` |

### build_common_module_context

```python
build_common_module_context(
    entry: CommonModuleEntry,
    root: Path,
) -> CommonModuleContext
```

BSL читается через:

```python
path.read_text(encoding="utf-8")
```

Системная кодировка не используется.

| Состояние | `bsl_text` | `read_status` |
|---|---|---|
| непустой UTF-8 | `str` | `ok` |
| пустой файл | `""` | `empty` |
| файл отсутствует | `None` | `missing` |
| ошибка UTF-8 или `OSError` | `None` | `read_error` |

`metadata["bsl_path"]` содержит только относительный POSIX-путь. Текст ошибки
и абсолютный локальный путь в контекст не попадают.

Абсолютные пути и пути с компонентом `..` в `CommonModuleEntry.bsl_path`
отклоняются.

## Граница с формами

CommonModule не индексируется функцией `scan_forms()`: на трёх проверенных
выгрузках найдено 2216, 76 и 3738 форм и ноль CommonModule entries.

Pilot не изменяет:

- `FormEntry`, `FormScanIndex` и `scan_forms()`;
- `FormContext` и `build_form_context()`;
- `elem_parser`;
- `object_decoder`;
- `REFERENCE_TYPE_PREFIXES`;
- коды `scan_forms.scan_warnings`.

`FORM_MODULE_MISSING` не переиспользуется. Для CommonModule применяется
отдельный typed status `ok | empty | missing | read_error`.

`elem_parser` неприменим, поскольку у общего модуля нет формы и `elem.json`.
`object_decoder` не вызывается искусственно, поскольку object JSON не нужен
для BSL-only контекста. Второй parser raw-header не создаётся.

## Реальная проверка

Обезличенный прогон трёх выгрузок:

| Выгрузка | Всего | `ok` | `empty` | `missing` | `read_error` |
|---|---:|---:|---:|---:|---:|
| A | 651 | 649 | 1 | 1 | 0 |
| B | 4 | 4 | 0 | 0 | 0 |
| C | 242 | 241 | 0 | 1 | 0 |

Повторные прогоны дали одинаковые результаты. Дубликатов относительных путей
и абсолютных путей в entries или metadata не обнаружено.

## Пример

```bash
python examples/common_modules.py /path/to/cf_export --runs 2
```

Пример печатает только агрегаты. Имена модулей, BSL-текст и абсолютные пути
в вывод не попадают.
