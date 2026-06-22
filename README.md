# v8unpack-agent

Надстройка над [v8unpack](https://github.com/saby-integration/v8unpack) для
агентных / LLM-пайплайнов по конфигурациям 1С.

Реализует доработки из статьи **«Обычные формы 1С в агентном пайплайне:
пошаговая распаковка»**: распаковщик `Form.bin` сообщество уже написало, а этот
пакет добавляет то, чего не хватает агенту вокруг него — фабрику путей по
конвенции, артефакт распаковки с явным флагом полноты, реестр актуальности и
встраивание распаковки в индексацию как pre-step.

**Этот пакет сам не разбирает бинарные формы 1С.** Реальную распаковку
выполняет [v8unpack](https://github.com/saby-integration/v8unpack) (Python, MIT;
либо C++-порт [e8tools/v8unpack](https://github.com/e8tools/v8unpack)). Здесь —
обвязка: *куда* класть результат, *насколько* он полон и *не устарел* ли он.

## Кто что решает

1. **`v8unpack` (upstream)** — превращает контейнеры `cf/cfe/epf/erf` в
   человекочитаемое дерево файлов и выносит **код** обычных форм в
   отдельные файлы. Его собственное ограничение: разметка форм и часть свойств
   остаются нечитаемыми.
2. **`v8unpack-agent` (этот пакет)** — не трогает бинарное содержимое. Поверх
   распаковщика выстраивает трёхэтапный пайплайн под кейс агента.

## Трёхэтапный пайплайн

```
index_cf(<путь_к_выгрузке>)
  └─► 1) unpack_all_forms()        # v8unpack по всем Form.bin → текстовый слой
       └─► 2) update_forms_index() # JSON-карта актуальности (контроль рассинхрона)
            └─► 3) rag.rebuild()   # code_context() видит код форм
```

- **Идемпотентность.** Повторный прогон не перекладывает формы, у которых
  `bin_mtime == unpacked_mtime` — только новые/изменённые.
- **Отказоустойчивость.** Если по одной форме `extraction_ok=False` — пайплайн
  не падает, реестр честно помечает её как частичную.
- **Прозрачность для агента.** Со стороны индексации это просто ещё один
  источник текстов.

## Внешние отчёты (.erf)

`.erf`-файлы распаковываются в два шага:

```
my_report.erf
  └─► unpack_erf() / v8unpack.extract() → текстовый слой
       └─► extract_skd_queries()         → skd_queries.json
```

```python
from pathlib import Path
from v8unpack_agent import unpack_erf, extract_skd_queries

result = unpack_erf(Path("my_report.erf"), Path("text_layer/my_report"))
skd    = extract_skd_queries(Path("text_layer/my_report"))

print(skd.skd_extracted)   # True
for ds in skd.datasets:
    print(ds["name"], "|", ds["query"][:60])
```

Если `skd_extracted=False` — агент видит только BSL модуля отчёта. Это не
ошибка пайплайна, а сигнал о неполноте контекста (отчёт без СКД или
нераспознанный формат `Template.bin`).

## Публичная поверхность

| Модуль | Что даёт |
|---|---|
| `form_paths` | Фабрика путей по конвенции: `form_paths()`, `item_modules()` (вложенные панели из `Items/`), `all_module_paths()`. Чистая арифметика путей — файлы не читаются. |
| `form_artifact` | `FormArtifact` (`name`, `paths`, `extraction_ok`, `extraction_warnings`) — результат распаковки одной формы с явным флагом полноты, без тихого провала. |
| `forms_index` | `FormsIndex` / `FormsIndexEntry` + `is_form_stale()` — реестр актуальности по `bin_mtime` vs `unpacked_mtime`. |
| `pipeline` | `discover_form_bins()`, `unpack_all_forms()`, `update_forms_index()`, `unpack_erf()`, `ErfUnpacker` — распаковка форм и `.erf` как pre-step индексации. |
| `skd_extractor` | `extract_skd_queries()`, `SkdResult` — извлечение запросов СКД из `Template.bin` распакованного `.erf`; результат пишется в `skd_queries.json` рядом с корнем. |

### Конвенция путей (формы)

```
<unpacked_root>/Form/<имя>/Form.obj.bsl          # код самой формы
<unpacked_root>/Form/<имя>/Ext/ObjectModule.bsl  # модуль объекта
<unpacked_root>/Form/<имя>/Form.json             # метаданные формы
<unpacked_root>/Form/<имя>/Items/                # вложенные панели/группы
```

### Конвенция путей (внешние отчёты)

```
<unpacked_root>/
  ExternalDataProcessor.obj.bsl                  # BSL модуль отчёта
  Template/
    ОсновнаяСхемаКомпоновкиДанных/
      Template.bin                               # v8-контейнер с XML СКД
<unpacked_root>.parent/
  skd_queries.json                               # извлечённые запросы СКД
```

`Template.bin` — v8-контейнер с переменным заголовком (8 или 24 байта в
зависимости от версии платформы) + UTF-8 BOM + XML схемы компоновки данных.
`extract_skd_queries()` определяет начало XML динамически — по BOM, а при его
отсутствии по `<?xml`.

### Запись `forms_index`

| Поле | Тип | Значение |
|---|---|---|
| `bin_path` | string | Путь к исходному `Form.bin` относительно корня выгрузки |
| `unpacked_root` | string | Путь к директории с распакованными текстами |
| `bin_mtime` | float | Unix-время изменения `Form.bin` в момент распаковки |
| `unpacked_mtime` | float | Unix-время последней распаковки; если `bin_mtime > unpacked_mtime` — форма устарела |
| `extraction_ok` | bool | `true` — полная распаковка; `false` — частичная |
| `warnings` | array | Диагностические сообщения при частичной распаковке; пустой массив при `extraction_ok: true` |

`forms_index` — **не** источник истины (источник — `Form.bin` в выгрузке), а
**карта актуальности**. В неё кладётся только маршрутизация и метки времени:
никакого содержимого `Form.bin`, строк подключения, имён баз/хостов — реестр
остаётся обезличенным и коммитится в репозиторий вместе с выгрузкой.

## Установка

Пока не опубликовано в PyPI. Установка из репозитория:

```bash
# v8unpack с поддержкой .erf (PR#29, до мержа в saby-integration/v8unpack):
pip install git+https://github.com/MRDK80/v8unpack.git@fix/external-report-support
pip install git+https://github.com/MRDK80/v8unpack-agent.git
```

> После принятия [PR#29](https://github.com/saby-integration/v8unpack/pull/29)
> замените первую строку на `pip install "v8unpack>=1.2.10"`.

или из локального checkout:

```bash
pip install -e .
```

## Быстрый старт

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import (
    FormArtifact,
    form_paths,
    unpack_all_forms,
    update_forms_index,
    is_form_stale,
)

dump_root = Path("unpacked_cf/")     # дерево, полученное от v8unpack
unpacked_root = Path("text_layer/")  # куда складываем текстовый слой форм


def unpack_one(bin_path: Path, root: Path, form_name: str) -> FormArtifact:
    """Распаковать одну форму через v8unpack и вернуть артефакт.

    Реальную распаковку делает v8unpack; здесь решается только полнота
    результата и заполняется FormArtifact по конвенции путей.
    """
    target = root / "Form" / form_name
    target.mkdir(parents=True, exist_ok=True)
    v8unpack.extract(str(bin_path), str(target))

    paths = form_paths(root, form_name)
    if paths["object_module"].exists():
        return FormArtifact.for_form(root, form_name)
    return FormArtifact.for_form(
        root, form_name,
        extraction_ok=False,
        extraction_warnings=["код формы не распакован"],
    )


# 1) распаковываем все формы выгрузки
artifacts = unpack_all_forms(dump_root, unpacked_root, unpack_one)

# 2) обновляем карту актуальности
index = update_forms_index(dump_root, unpacked_root, artifacts)
index.save(Path("forms_index.json"))

# 3) позже — узнаём, какие формы устарели и требуют перераспаковки
for name in index.stale_forms():
    print("устарела:", name)
```

Полный пример: [`examples/basic_usage.py`](examples/basic_usage.py).

## Тесты

```bash
pip install -e ".[test]"
pytest
```

Набор тестов полностью синтетический: проверка идёт на временных файловых
деревьях с внедрённым распаковщиком-заглушкой, так что реальный контейнер 1С
не требуется.

## Связанное

- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — нижележащий распаковщик контейнеров (Python, MIT)
- [e8tools/v8unpack](https://github.com/e8tools/v8unpack) — C++-порт (MPL-2.0), поддерживает `.erf`
- [PR#29 — fix: add ExternalReport (.erf) support](https://github.com/saby-integration/v8unpack/pull/29)
- Статья: «Обычные формы 1С в агентном пайплайне: пошаговая распаковка»

## Лицензия

MIT

---

Материал независимый, примеры синтетические/обезличенные; рабочие данные и
внутренняя инфраструктура не используются.
