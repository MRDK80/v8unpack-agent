# Доля `elem_index_ok=False` по штатно распакованному `.cf` (issue #229)

Дата замера: 2026-09-09. Класс задачи: research. Production-код не изменялся.

## Question

Какая доля обычных форм остаётся с `elem_index_ok=False` после fallback #100,
#103, #107 и #108, как эти формы распределены по `FormClass` и по
`UnindexedReason`, и требует ли каждый класс исправления.

Вопрос вынесен из #150, где класс `unknown_share_unmeasured` получил решение
`insufficient evidence`: одиночный `Form.bin` не является входом upstream, и на
нём текстовый слой не создаётся вообще.

## Input contract and corpus alias

| Параметр | Значение |
|---|---|
| Corpus alias | `D` |
| Режим сбора | конфигурация сохранена в `.cf`, затем распакована штатной точкой входа upstream |
| `*.elem.json` в дереве | 2216 |
| `*.obj.bsl` в дереве | 3704 |
| `Form.bin` в дереве | 0 |
| `*.xml` в дереве | 0 |
| upstream `v8unpack` | 1.2.11 |
| commit инструмента | `30b549cca476f0f86681357a7923c148424c26a9` |

Alias `D` введён намеренно. Число форм совпадает с корпусом, зафиксированным в
docstring `UnindexedReason` после #108, однако `Form.bin` в дереве отсутствует,
тогда как для корпуса `A` в #227 зафиксировано 1354 файла `Form.bin`.
Тождественность корпусов не доказана, поэтому отдельный alias.

## Method and denominator

Замер выполнен существующим отчётом `examples/unindexed_forms_report.py`,
расширенным в рамках #229 режимами `--json` и `--runs`. Новый одноразовый
скрипт не создавался.

Cohort и denominator:

```text
forms_total = число кандидатов *.elem.json в дереве распаковки
unit        = elem_json_candidate
```

Выбор unit обусловлен сопоставимостью: исторический screening строился тем же
обходом `rglob("*.elem.json")`. Перекрёстная проверка `scan_forms()` на том же
корне дала 2216 записей, все с непустым `elem_json_path`, то есть на корпусе
`D` cohort по кандидатам и cohort сканера форм совпадают.

Итог для каждой формы взаимно исключающий:

```text
elem_index_ok=True
elem_index_ok=False
explicitly_excluded
```

Инвариант баланса проверяется до вывода агрегата:

```text
forms_total == ok + failed + excluded
2216        == 2174 + 42 + 0
```

Классификация выполняется только каноническими функциями:

* `parse_elem_json()` — признак `elem_index_ok`;
* `classify_unindexed_form()` и enum `UnindexedReason` — причина;
* `calc_data_path_coverage()` — `FormClass` проиндексированной формы;
* `classify_no_widgets_form()` — уточнение класса для `no_tabular_no_widgets`;
* `FormClass.UNKNOWN` — для остальных непроиндексированных форм, как в
  `coverage_metric`.

Группировка причин по тексту предупреждений не применяется.

## Determinism

Выполнено два прогона на одном и том же дереве и одном commit инструмента.
Сравнивался нормализованный агрегат целиком: cohort, счётчики индексации,
`FormClass`, `UnindexedReason`, матрица и excluded. Временных полей,
длительностей и путей в агрегате нет, поэтому нормализация не потребовалась.

```text
aggregate_signature = 8be7db3be301b7b6
```

Подпись — sha256 канонического JSON агрегата, первые 16 hex. Оба прогона дали
одинаковую подпись.

## Aggregate results

| Метрика | Значение |
|---|---:|
| `forms_total` | 2216 |
| `elem_index_ok=True` | 2174 |
| `elem_index_ok=False` | 42 |
| Доля `elem_index_ok=False` | 1.8953% |
| `excluded` | 0 |

## FormClass distribution

| FormClass | Форм | Доля |
|---|---:|---:|
| `service` | 1994 | 89.98% |
| `object` | 197 | 8.89% |
| `unknown` | 25 | 1.13% |

Все 25 форм класса `unknown` — непроиндексированные. Остаток непроиндексированных,
17 форм, получил класс `service` через `classify_no_widgets_form()`. Ни одна
проиндексированная форма не осталась в классе `unknown`.

## UnindexedReason distribution

| UnindexedReason | Форм | Доля от cohort | FormClass |
|---|---:|---:|---|
| `no_tabular_no_widgets` | 17 | 0.77% | `service` 17 |
| `tabular_field_bsl_source_mismatch` | 11 | 0.50% | `unknown` 11 |
| `tabular_field_platform_dynamic` | 7 | 0.32% | `unknown` 7 |
| `tabular_field_programmatic_no_defs` | 5 | 0.23% | `unknown` 5 |
| `no_owner_object` | 2 | 0.09% | `unknown` 2 |
| `no_legacy_json` | 0 | 0.00% | — |
| `tabular_field_empty_attr_map` | 0 | 0.00% | — |
| `tabular_field_no_uuid_hits` | 0 | 0.00% | — |
| `unknown` | 0 | 0.00% | — |

Значение `unknown` равно нулю: `classify_unindexed_form()` не перешёл в ветку
перехвата исключений ни на одной форме корпуса.

## Comparison with baselines

| Corpus | Режим сбора | Denominator | Failed | Failed % | Примечание |
|---|---|---:|---:|---:|---|
| #98 | исторические каталоги форм | 2231 | 80 | 3.59% | пустой `tree`, предупреждение об отсутствии элементов |
| Снапшот после #108 | live-база | 2216 | 47 | 2.12% | C 17, MISMATCH 11, PROGRAMMATIC 8, PLATFORM 7, NO_OWNER 2, A 2 |
| Screening `C` | prepared dump | 3738 | 58 | 1.55% | 56 + 2, `Form.bin` 3422 |
| `D`, issue #229 | prepared `.cf` dump | 2216 | 42 | 1.8953% | `Form.bin` 0, `*.xml` 0 |

Объяснение расхождений:

* Относительно снапшота после #108 расхождение объяснено полностью и без
  гипотез. Denominator тот же, 2216. Ушли обе формы класса
  `tabular_field_empty_attr_map` и три формы класса
  `tabular_field_programmatic_no_defs`, а число проиндексированных выросло ровно
  на пять: 2169 против 2174. Баланс дельты сходится: 2 + 3 == 5.
* Относительно #98 сравнение приблизительное: 2231 против 2216 каталогов форм,
  то есть иной срез. Снижение доли с 3.59% до 1.8953% не трактуется как
  улучшение парсера.
* Относительно screening `C` количественное сравнение неприменимо: 3738 форм и
  3422 `Form.bin` против 2216 форм и нуля `Form.bin`, то есть другой режим
  сбора и другой состав входа.

Provenance-уточнение к постановке #229: числа 3738 и 3680 относятся к screening
конфигурации `C`, а не к отчёту #163. Отчёт #163 выполнен на выгрузке `A` и
фиксирует 2216 форм, 2054 с `object_attributes` и 162 `no_owner_object`.

## Decision matrix

| UnindexedReason | Форм | Решение | Основание |
|---|---:|---|---|
| `no_tabular_no_widgets` | 17 | keep unknown | форма без виджетов данных: регистры и сервисные формы, поведение корректно |
| `tabular_field_bsl_source_mismatch` | 11 | keep unknown | объявления колонок принадлежат другому источнику; сопоставление по имени дало бы фантомные колонки (#107) |
| `tabular_field_platform_dynamic` | 7 | keep unknown | колонки формирует платформа динамически, статический разбор невозможен by design (#107) |
| `tabular_field_programmatic_no_defs` | 5 | insufficient evidence | колонки не объявлены ни в виджете, ни в модуле формы; нового доказанного источника данных нет |
| `no_owner_object` | 2 | keep unknown | общая форма не имеет объекта-владельца по дизайну платформы (#108) |
| `no_legacy_json` | 0 | insufficient evidence | класс на корпусе не встретился |
| `tabular_field_empty_attr_map` | 0 | insufficient evidence | класс на корпусе не встретился |
| `tabular_field_no_uuid_hits` | 0 | not applicable | после #107 значение не возвращается, сохранено для совместимости |
| `unknown` | 0 | not applicable | ветка перехвата исключений не срабатывала |

Ни один класс не получил решение `implement` или `upstream issue`.

## Limitations

* Замер выполнен на одном корпусе и одном режиме сбора. Обобщение на другие
  конфигурации не доказано.
* Тождественность корпуса `D` корпусам `A` и `#98` не доказана; сопоставление
  ведётся только по denominator и распределению.
* Cohort ограничен кандидатами `*.elem.json`. Формы, для которых кандидат не
  создан на стадии распаковки, в denominator не входят по определению.
* Класс `tabular_field_programmatic_no_defs` остаётся неразрешённым: причина
  установлена, но достаточных данных для безопасной семантики нет.
* Значение `UnindexedReason.UNKNOWN` семантически перегружено: оно означает сбой
  классификатора, а не отдельный layout. На корпусе `D` не встретилось.

## Conclusion

Доля `elem_index_ok=False` на штатно распакованном `.cf` измерена и
воспроизводима: 42 формы из 2216, то есть 1.8953%, подпись агрегата
`8be7db3be301b7b6`. Вся доля раскладывается на пять классов, из которых четыре
корректны по дизайну платформы или по решению #107 и #108, а один остаётся с
недостаточными доказательствами. Исходный вопрос #150 о неизмеренной доле
`unknown` закрыт: скрытого дефекта парсера на этом корпусе не обнаружено.
