# research: формы без `object_attributes` (issue #163)

- Issue: #163 · Роадмап: #157 · Границы: #160, #151 · Не дублирует #143
- Базовая ветка: `main`, базовый commit `60c5c1c`
- Инструмент: `examples/missing_object_attributes_report.py` (требует реальную выгрузку)
- Production-код не изменён.

## 1. Baseline

| Метрика | Значение |
|---|---|
| Форм всего | 2216 |
| С `object_attributes` | 2054 |
| Без `object_attributes` | 162 (7.3%) |
| Прогонов | 2, подпись агрегата совпала |

Подпись агрегата (sha256/16): `788bd54b0fe931d0`

## 2. Метод определения точки отказа

Для каждой формы без `object_attributes`:

```
path = object_json_path(entry)
if path is None:            point = "object_json_not_found"
elif decode.ok:             point = "unexpected_context_none"
else:                       point = f"decode_error:{decode.error.value}"
```

| Точка отказа | Форм | Доля |
|---|---:|---:|
| `object_json_not_found` | 0 | 0.0% |
| `unexpected_context_none` | 0 | 0.0% |
| `decode_error:header_missing` | 162 | 100.0% |

Распределение по `DecodeError`: `header_missing` 162, `json_not_found` 0,
`json_parse_error` 0, `version_unsupported` 0.

## 3. Структура CommonForm и роль найденного JSON

Проектные факты (main, `60c5c1c`):

- `scan_forms` знает два config-layout: 4-уровневый `<Тип>/<Объект>/<Контейнер>/<Форма>`
  и 3-уровневый `CommonForm/<Форма>` без уровня `ObjectName`
  (`object_name == ""`, `container_name == "CommonForm"`).
- `catalog_resolver.object_json_path()` поднимается ровно на 2 уровня вверх от
  `form_entry.form_path` и ищет `.json` по имени каталога объекта, затем
  fallback по имени типа.
- Для 4-уровневого layout 2 уровня вверх дают каталог объекта `<Тип>/<Объект>`.
- Для 3-уровневого layout 2 уровня вверх дают **корень выгрузки** — каталога
  объекта на этом уровне не существует по конструкции.

Нормализованное описание (реальные пути не публикуются):

```
3-level layout: CommonForm/<Form>
object_name level : absent
owner object file : absent by layout
levels_up         : 2
selected candidate: <root>/<candidate>.json
candidate role    : <export_root_neighbour | form_artifact | ...>
distinct candidates for 162 forms: 1
```

Проверяемое предсказание: если кандидат — сосед на уровне корня, то у всех 162
форм путь-кандидат **один и тот же**, то есть `distinct candidates == 1`. Это
исключает трактовку найденного файла как объекта-владельца формы.

`HEADER_MISSING` в этом случае означает лишь отсутствие ключа `header` в
файле, который объектом-владельцем не является.

## 4. Контроли

| Контроль | Layout | Ожидание | Факт |
|---|---|---|---|
| A — объект-владелец есть | 4-level Catalog/Document | `ok == True`, `object_attributes is not None` | `role=owner_object_file`, `decode.ok=True` — ожидание подтверждено, провалов 0 |
| B — CommonForm | 3-level, `ObjectName` отсутствует | владельца нет; кандидат не является доказательством владельца | `object_name=absent`, `role=export_root_neighbour`, класс `no_owner_object` — ожидание подтверждено |
| C — повреждённый owner JSON | 4-level, владелец существует | `HEADER_MISSING` на настоящем owner JSON | `error=header_missing`, `role=owner_object_file`, класс `layout_unsupported` — ожидание подтверждено |

Контроль C отличает подлинный `layout_unsupported` от ожидаемого отсутствия
владельца у CommonForm.

## 5. Разрез по FormClass

| FormClass | Форм | Доля |
|---|---:|---:|
| `service` | 158 | 97.5% |
| `object` | 0 | 0.0% |
| `unknown` | 4 | 2.5% |

Сопоставление с существующими причинами: `NO_OWNER_OBJECT` (#108),
`NO_TABULAR_NO_WIDGETS` (#109) — `NO_OWNER_OBJECT` согласуется: для `CommonForm` он закреплён by design в `classify_unindexed_form`; соответствие `NO_TABULAR_NO_WIDGETS` в прогоне #163 не измерялось.

`service` поддерживает решение `keep as is`, но не заменяет структурного
доказательства отсутствия владельца.

## 6. Итоговая классификация

| Класс | Форм | Доля | #160 | #151 | Решение |
|---|---:|---:|---|---|---|
| `no_owner_object` | 162 | 100.0% | нет | нет | keep as is |
| `type_out_of_scope` | 0 | 0.0% | нет | да | follow-up |
| `layout_unsupported` | 0 | 0.0% | да | возможно | follow-up |
| `path_convention_miss` | 0 | 0.0% | нет | возможно | implementation issue |
| `broken_json` | 0 | 0.0% | нет | нет | RCA/upstream |
| `insufficient_evidence` | 0 | 0.0% | неизвестно | неизвестно | keep as is |

Каждая из 162 форм учтена ровно в одном классе.

## 7. Разграничение с #160 и #151

- случаев #160: 0 — привязка требует одновременно: найденный JSON
  является объектом-владельцем, структура нормализована, есть объектные
  `Properties`/`TabularSections`, отсутствие `header` — единственная причина отказа.
- случаев #151: 0 — требует существующего владельца с типом вне охвата.
- `no_owner_object` by design: 162 из 162 (100.0%).

Если файл относится к самой CommonForm либо к корню выгрузки, passthrough
нормализованного layout из #160 был бы ложным исправлением: форма или корень
выгрузки были бы выданы за объект метаданных.

## 8. Обезличенность и детерминированность

- Отчёт печатает только количества, коды отказа, типы и структурные роли.
- Реальные имена, UUID, абсолютные пути и CSV в git не попадают; `*.csv` в `.gitignore`.
- Режимы `--local-names` и `--csv` — только локально, результаты в PR и issue не вставляются.
- Два прогона дают совпадающую подпись агрегата.
- `pytest -q`: 857 passed (прогон исследования #163, PR #169).

## 9. Открытый вопрос для follow-up

`object_json_path()` возвращает `Path` для layout, в котором объекта-владельца
нет по конструкции. Если предсказание из раздела 3 подтвердится, это отдельный
контракт-дефект (возврат «соседнего» файла вместо `None`), а не проблема
декодера. Implementation issue создаётся только после доказательства общего
контракта внутри класса.

## Follow-up #172

Предсказание подтверждено на той же выгрузке: `object_json_not_found` 0 → 162,
`decode_error:header_missing` 162 → 0, `unexpected_context_none` 0 → 0.
Число форм с `object_attributes` не изменилось: 2054 из 2216. Класс
`no_owner_object` остаётся 162, решение `keep as is` в силе. Роль кандидата
`export_root_neighbour` исчезает из распределения: посторонний файл первого
уровня выгрузки больше не выбирается.

## Addendum от 6 сентября 2026 — источники агрегатов

Historical observation: разделы 1–9 отражают состояние на 23 августа 2026 и
не переписываются задним числом. Ниже — только привязка ранее пустых полей к
опубликованным источникам (issue #183).

Current interpretation:

| Поле | Значение | Источник |
|---|---|---|
| подпись агрегата (sha256/16) | `788bd54b0fe931d0` | итоги #163, два прогона |
| `levels_up` | 2 | итоги #163, разбор `object_json_path()` |
| distinct candidates на 162 формы | 1 | итоги #163 |
| контроли A/B/C | ожидания подтверждены, провалов 0 | итоги #163, `--controls` |
| `FormClass`: `service` / `object` / `unknown` | 158 / 0 / 4 | приёмка #163 |
| `no_owner_object` | 162 (100.0%) | итоги и приёмка #163 |
| `path_convention_miss`, `insufficient_evidence` | 0 (0.0%) | итоги #163 |
| случаев #160 и #151 | 0 | итоги #163, подтверждено после #172 и в #180 |
| `pytest -q` на момент исследования | 857 passed | PR #169 |

Нули означают измеренное отсутствие случаев в опубликованном прогоне #163, а не
отсутствие измерения. Соответствие причине `NO_TABULAR_NO_WIDGETS` (#109) в
прогоне #163 не измерялось и здесь не выводится.

Агрегаты выгрузки A не смешиваются с данными третьей конфигурации из
`docs/research/third_configuration_validation.md`. Решение `keep as is` не
пересматривается.
