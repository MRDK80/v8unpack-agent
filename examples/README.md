# Примеры

Все примеры обезличены: реальные выгрузки, имена баз, хосты и строки
подключения в репозитории не хранятся.

Примеры делятся на две группы по требованиям к входным данным. Различие
существенно при проверке изменений: первая группа запускается «как есть» и
годится для регрессионного прогона, вторая без выгрузки принципиально не
стартует и проверяется только вручную на машине с данными.

## Самодостаточные (запускаются без аргументов)

Создают синтетические данные во временном каталоге и удаляют их за собой.
Установленная платформа 1С и выгрузка не требуются.

| Файл | Что показывает |
|---|---|
| `basic_usage.py` | базовый сценарий индекса форм и проверки устаревания |
| `chain_form_bindings.py` | сегментные цепочки вложенных `data_path` |
| `coverage_metric.py` | метрика покрытия по элементам данных |
| `form_bindings.py` | подтверждённые привязки `data_path` элементов |
| `form_context.py` | `FormContext` и компактный фрагмент для промпта |
| `reference_types.py` | резолюция `Ref#uuid` в читаемое имя типа |
| `unindexed_forms_report.py` | отчёт по неиндексируемым формам |
| `zero_binding_reasons.py` | машиночитаемые причины нулевой привязки |

Проверить группу целиком:

```bash
for f in examples/basic_usage.py examples/chain_form_bindings.py \
         examples/coverage_metric.py examples/form_bindings.py \
         examples/form_context.py examples/reference_types.py \
         examples/unindexed_forms_report.py examples/zero_binding_reasons.py; do
    python "$f" > /dev/null || echo "FAIL $f"
done
```

## Требующие реальной выгрузки

Имеют обязательные аргументы командной строки. Запуск без выгрузки штатно
завершается ошибкой `argparse` — это ожидаемое поведение, а не дефект. В
автоматический прогон эти файлы не входят.

| Файл | Обязательные аргументы | Источник данных |
|---|---|---|
| `extract_skd_queries.py` | `--unpack-dir`, `--output` | распакованный внешний отчёт `.erf` |
| `legacy_list_form_bindings.py` | `FORM_DIR` | каталог формы из выгрузки v8unpack |
| `missing_object_attributes_report.py` | `EXPORT_ROOT` | корень выгрузки `cf_export` конфигурации |

Формулировка «проверены все файлы `examples/`» в отчётах о задачах относится
только к первой группе, если явно не указано, что прогон выполнялся на
выгрузке.

### `missing_object_attributes_report.py` (issue #163)

Классифицирует формы без `FormContext.object_attributes` по доказуемым классам
причин: `no_owner_object`, `type_out_of_scope`, `layout_unsupported`,
`path_convention_miss`, `broken_json`, `insufficient_evidence`. Класс
назначается только по структурным признакам — расположению найденного JSON
относительно формы, объекта и корня выгрузки, — а не по имени формы.
Production-код не используется на запись.

```bash
python examples/missing_object_attributes_report.py /path/to/cf_export --runs 2
python examples/missing_object_attributes_report.py /path/to/cf_export --controls
```

| Режим | Назначение |
|---|---|
| `--runs N` | N прогонов; агрегат сводится в подпись sha256/16, расхождение даёт код возврата 1 |
| `--controls` | контроли A (форма с владельцем), B (общая форма), C (реальный owner JSON без `header`) |
| `--local-names` | **только локально**: печать реальных имён форм |
| `--csv PATH` | **только локально**: построчная таблица |

Вывод по умолчанию обезличен: печатаются количества, коды `DecodeError`, типы
метаданных, структурные роли и нормализованные ключи путей вида
`<root>/<L1>/<candidate>.json`. Имена форм, имена объектов, UUID и абсолютные
пути не выводятся.

Результаты `--local-names` и `--csv` в PR, issue и коммиты не попадают; `*.csv`
остаётся под `.gitignore`. Отчёт исследования: `docs/research/missing_object_attributes_issue163.md`.
