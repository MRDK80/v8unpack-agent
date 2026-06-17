# v8unpack-agent

Оркестрационный слой-обёртка над [v8unpack](https://github.com/saby-integration/v8unpack)
для использования внутри автоматизированного / LLM-агентного пайплайна.

**Этот пакет сам не разбирает бинарные формы 1С.** Реальную распаковку выполняет
[v8unpack](https://github.com/saby-integration/v8unpack); данный пакет лишь решает,
*куда* положить результат и *не устарела* ли предыдущая распаковка. Если вам нужен
инструмент, который делает `Form.bin` читаемым, — вам нужен `v8unpack`, а не это.

| Модуль | Что делает |
|---|---|
| `extractor` | Протокол `BinaryExtractor` + `ExtractionResult` (явный флаг `extraction_ok`, без тихого провала). Это *контракт*, а не парсер. |
| `shadow_tree` | OS-нейтральная фабрика путей: отображает `<source_root>/…/Form.bin` → `<shadow_root>/…/Form/`. Чистая арифметика путей — содержимое файлов не читается. |
| `sync_index` | `ShadowIndex` — детект дрейфа по `mtime + size`; `DriftReport`. Читает только метаданные файла, не содержимое. |

## Кто что решает

Два слоя:

1. **`v8unpack` (upstream)** — превращает контейнеры `cf/cfe/epf` в
   человекочитаемое дерево файлов. Он уже выносит **код** форм в отдельные
   файлы («видны изменения элементов форм»). Его собственное задокументированное
   ограничение: **разметка** форм и **свойства** объектов остаются нечитаемыми.
2. **Этот пакет (`v8unpack-agent`)** — **не** трогает бинарное содержимое
   вообще. Он добавляет обвязку, нужную агенту вокруг `v8unpack`:
   - стабильный протокол `BinaryExtractor`, чтобы конкретный экстрактор можно
     было внедрить, не «протекая» режимами отказа (`V8UnpackExtractor` —
     эталонная реализация);
   - OS-нейтральную фабрику shadow-путей (`shadow_tree`);
   - индекс дрейфа по `mtime + size` (`sync_index`), чтобы агент понимал, когда
     ранее созданная «тень» устарела и её нужно перестроить.


Итого: если ваша цель — «читать *код* обычных форм», `v8unpack` это уже умеет, а
данный пакет просто оркестрирует его для агента. 
## Установка

Пока не опубликовано в PyPI. Установка из репозитория:

```bash
pip install "v8unpack>=1.2"
pip install git+https://github.com/MRDK80/v8unpack-agent.git
```

или из локального checkout:

```bash
pip install .
```

## Эталонный экстрактор

В пакет входит конкретный опциональный экстрактор поверх `v8unpack` —
`V8UnpackExtractor`, который удовлетворяет протоколу `BinaryExtractor` и соблюдает
контракт «без тихого провала» (при ошибке или пустом выводе возвращается
`extraction_ok=False` с заметками, а не выбрасывается исключение):

```python
from pathlib import Path
from v8unpack_agent import V8UnpackExtractor, ShadowTreeLayout, shadow_path_for

extractor = V8UnpackExtractor()
layout = ShadowTreeLayout(
    binary_source_root=Path("unpacked_cf/"),
    shadow_tree_root=Path(".shadow/"),
)

for binary in layout.binary_source_root.rglob("*.bin"):
    target = shadow_path_for(binary, layout)
    result = extractor.extract(binary, target)
    if not result.extraction_ok:
        print("degraded:", result.notes)
```

## Быстрый старт

```python
from pathlib import Path
import v8unpack
from v8unpack_agent import ShadowTreeLayout, shadow_path_for, ShadowIndex

# 0. Сначала v8unpack превращает контейнер в дерево файлов
#    (именно здесь лежат бинарники форм *.bin).
source_cf = Path("demo.cf")
unpacked_root = Path("unpacked_cf/")
v8unpack.extract(str(source_cf), str(unpacked_root))

# 1. Настраиваем layout: распакованное дерево — источник бинарников, тени — отдельно.
layout = ShadowTreeLayout(
    binary_source_root=unpacked_root,
    shadow_tree_root=Path(".shadow/"),
)

# 2. shadow_path_for сопоставляет каждому *.bin его целевой каталог-тень.
binaries = list(layout.binary_source_root.rglob("*.bin"))
for binary in binaries:
    target = shadow_path_for(binary, layout)
    # ... передайте `target` своему конкретному BinaryExtractor ...

# 3. Строим индекс дрейфа по тем же бинарникам, что только что нашли.
index = ShadowIndex.build(layout.binary_source_root, binaries)
index.save(Path(".shadow_index.json"))

# 4. Позже: проверяем дрейф относительно записанного снимка.
report = index.check_drift(layout.binary_source_root, binaries)
if not report.is_clean:
    print("Устаревшие тени:", report.changed)
```

## Тесты

```bash
pip install -e ".[test]"
pytest
```

Набор тестов полностью синтетический — `V8UnpackExtractor` проверяется на
внедрённом фейковом `v8unpack`, так что реальный контейнер 1С не требуется.

## Связанное

- [saby-integration/v8unpack](https://github.com/saby-integration/v8unpack) — нижележащий экстрактор, который собственно и распаковывает контейнеры
- Статья (черновик): оркестрация v8unpack внутри агентного пайплайна — тени с детектом дрейфа. Примеры только синтетические/демонстрационные.

## Лицензия

MIT
