# Инвентаризация BSL-модулей и layout выгрузок v8unpack (issue #202)

Исследование для эпика #201. Цель — зафиксировать на реальных выгрузках, какие
виды BSL-модулей 1С создаёт `v8unpack`, где они лежат, как различить
отсутствующий, пустой и непустой модуль и что из этого видит агент сейчас.
Production-код и публичные API в рамках задачи не менялись.

Машиночитаемая версия результатов: [`bsl_module_inventory_issue202.json`](bsl_module_inventory_issue202.json).

## Материал и метод

| Метка | Что это | Роль |
|---|---|---|
| A | конфигурация, normalized-layout `v8unpack` | основная выгрузка; для неё есть выгрузка Конфигуратором в файлы |
| B | конфигурация, normalized-layout `v8unpack` | малая независимая конфигурация |
| C | конфигурация, normalized-layout `v8unpack` | крупная независимая конфигурация |
| X1, X2 | расширение конфигурации, распаковка `saby v8unpack 1.2.13` | X1 — ранее сканированная копия, X2 — свежая распаковка того же `.cfe` |

A, B и C — те же три выгрузки, что в #151: число записей `scan_common_modules`
совпало (651 / 4 / 242). Внешние обработки (`.epf`) и внешние отчёты (`.erf`)
для исследования не предоставлялись.

Все прогоны выполнены владельцем на своей машине из корня репозитория на SHA
`5047a84788e1d65f64b1adce1f785ad76af6605d`; пакет `v8unpack_agent` импортировался из этого клона.
Сборщики агрегатов были временными скриптами вне репозитория. Они печатали
только агрегаты и перед печатью проверяли вывод на кириллицу, UUID,
абсолютные пути и обратную косую черту.

Правила обезличивания путей:

- каталог верхнего уровня (тип метаданных) сохраняется, если это ASCII-идентификатор;
- вложенный каталог сохраняется, если это контейнер (`<Type>Form`,
  `<Type>Command`, `Form`, `Template` и т. п.); иначе он заменяется на `{Name}`;
- UUID заменяются на `{uuid}`, числовые каталоги — на `{n}`, а суффиксы файлов
  (`obj`, `mgr`, `app`, `802`, `seance`, `con`) сохраняются как есть;
- основа имени файла сохраняется, только если совпадает с сохранённым
  контейнером или равна `Configuration` / `ConfigurationExtension`.

Статус чтения файла: `nonempty`, `empty` (0 байт или только BOM),
`whitespace_only`, `read_error` (ошибка UTF-8 или `OSError`). Отсутствие модуля
определяется отдельно — как отсутствие файла у существующего каталога объекта.

Текущее поведение агента измерено фактически: `scan_forms()` и
`scan_common_modules()` вызывались на каждой выгрузке, после чего проверялось,
какие реальные `*.bsl` попали в их результат.

Семантика суффиксов проверена на A. Нормализованный текст каждого `*.bsl`
v8unpack (без BOM, LF, без хвостовых пробелов) сравнивался с модулем того же
объекта в выгрузке Конфигуратором в файлы.

## Главные выводы

1. **`.802.bsl` — модуль обычного приложения, `.app.bsl` — модуль
   управляемого приложения.** На A текст `Configuration.802.bsl` точно
   совпал с `Ext/OrdinaryApplicationModule.bsl`, `Configuration.app.bsl` — с
   `Ext/ManagedApplicationModule.bsl`, `Configuration.con.bsl` — с
   `Ext/ExternalConnectionModule.bsl`, `Configuration.seance.bsl` — с
   `Ext/SessionModule.bsl`. Индекс Jaccard с остальными модулями не выше 0.067.
   Это совпадает с upstream `Configuration.ext_code` (`802: 0`, `app: 6`,
   `con: 5`, `seance: 7`). В B файла `Configuration.802.bsl` нет, то есть
   модуль обычного приложения не обязателен.
2. **Модули уровня конфигурации лежат в корне выгрузки**:
   `Configuration.<suffix>.bsl`, без каталога `Configuration/`.
3. **Суффикс `obj` зависит от типа владельца.** У справочников, документов,
   обработок, отчётов, планов обмена, ПВХ, бизнес-процессов и задач это модуль
   объекта. У регистров сведений и накопления — модуль набора записей
   (`RecordSetModule`). У констант — модуль менеджера значения
   (`ValueManagerModule`). **У перечислений `Enum.obj.bsl` — модуль менеджера**
   (`Enums/{Name}/Ext/ManagerModule.bsl`: 14 из 14 непустых совпали). У
   HTTP- и Web-сервисов — модуль сервиса. Поэтому `module_kind` нельзя
   выводить из одного суффикса: нужна пара «тип владельца + суффикс».
4. **Upstream не теряет непустые модули объектов, менеджеров, наборов записей,
   команд и сервисов.** Для всех неформенных шаблонов A число непустых модулей
   v8unpack равно числу непустых модулей Конфигуратора, а непустых модулей
   Конфигуратора без пары в v8unpack — 0.
5. **Отсутствие файла не всегда означает отсутствие модуля.** В A у
   `DataProcessors/{Name}/Ext/ObjectModule.bsl` Конфигуратор даёт 107 файлов,
   из них 25 пустых, а v8unpack — 101 файл, из них 17 пустых и 2 из одних
   пробелов. Шесть пустых модулей объекта обработки v8unpack не записал. У
   остальных неформенных шаблонов A общее число файлов совпадает. Для #203
   статус `missing` должен значить «файла нет», а не «модуля нет».
6. **Агент сейчас покрывает только формы и общие модули.** Модули уровня
   конфигурации, объектов, менеджеров, наборов записей, менеджеров значения,
   команд и сервисов обнаруживаются на диске, но агентом игнорируются.
7. **Сканер форм пишет в выгрузку.** Каждый прогон `scan_forms()` изменяет по
   одному JSON-файлу в каталоге формы; на свежей распаковке X2 первый прогон
   такой файл создаёт. Вероятный источник — запись
   `form_elements_index.json` в `v8unpack_agent/elem_parser.py`. BSL-агрегаты от
   этого не меняются. Для #203: универсальный индекс модулей должен только
   читать.

## Матрица обнаруженных видов

В ячейках A/B/C: `файлов (empty/whitespace_only)`; ошибок чтения и BOM на
всех выгрузках — 0. В колонке «Агент» указан сканер, который находит файл.

| Шаблон пути | owner_kind | module_kind | A | B | C | Агент | Статус | Доказательство |
|---|---|---|---:|---:|---:|---|---|---|
| `Configuration.con.bsl` | configuration | external_connection | 1 (0/0) | 1 (0/0) | 1 (0/0) | нет | ignored | Конфигуратор A, точное совпадение |
| `Configuration.app.bsl` | configuration | managed_application | 1 (0/0) | 1 (0/0) | 1 (0/0) | нет | ignored | Конфигуратор A, точное совпадение |
| `Configuration.802.bsl` | configuration | ordinary_application | 1 (0/0) | — | 1 (0/0) | нет | ignored | Конфигуратор A, точное совпадение |
| `Configuration.seance.bsl` | configuration | session | 1 (0/0) | 1 (0/0) | 1 (0/0) | нет | ignored | Конфигуратор A, точное совпадение |
| `CommonModule/{Name}/CommonModule.obj.bsl` | common_module | common_module | 650 (1/0) | 4 (0/0) | 241 (0/0) | `scan_common_modules` | supported | Конфигуратор A, совпадение текста |
| `AccumulationRegister/{Name}/AccumulationRegister.mgr.bsl` | metadata_object | manager | 3 (0/0) | — | 1 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Catalog/{Name}/Catalog.mgr.bsl` | metadata_object | manager | 65 (5/2) | — | 25 (6/1) | нет | ignored | Конфигуратор A, совпадение текста |
| `DataProcessor/{Name}/DataProcessor.mgr.bsl` | metadata_object | manager | 36 (10/0) | — | 11 (5/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Document/{Name}/Document.mgr.bsl` | metadata_object | manager | 74 (2/2) | — | 32 (9/1) | нет | ignored | Конфигуратор A, совпадение текста |
| `DocumentJournal/{Name}/DocumentJournal.mgr.bsl` | metadata_object | manager | 1 (0/0) | — | 1 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Enum/{Name}/Enum.obj.bsl` | metadata_object | manager | 24 (10/0) | — | 4 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `ExchangePlan/{Name}/ExchangePlan.mgr.bsl` | metadata_object | manager | 3 (0/0) | — | 8 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `InformationRegister/{Name}/InformationRegister.mgr.bsl` | metadata_object | manager | 83 (6/0) | — | 60 (17/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Report/{Name}/Report.mgr.bsl` | metadata_object | manager | 23 (1/0) | — | 34 (18/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `BusinessProcess/{Name}/BusinessProcess.obj.bsl` | metadata_object | object | 3 (0/0) | — | — | нет | ignored | Конфигуратор A, совпадение текста |
| `Catalog/{Name}/Catalog.obj.bsl` | metadata_object | object | 124 (33/1) | — | 89 (33/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `ChartOfAccounts/{Name}/ChartOfAccounts.obj.bsl` | metadata_object | object | — | — | 1 (0/0) | нет | ignored | upstream `ext_code` + аналогия layout |
| `ChartOfCalculationTypes/{Name}/ChartOfCalculationTypes.obj.bsl` | metadata_object | object | — | — | 1 (0/0) | нет | ignored | upstream `ext_code` + аналогия layout |
| `ChartOfCharacteristicType/{Name}/ChartOfCharacteristicType.obj.bsl` | metadata_object | object | 3 (1/0) | — | 3 (1/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `DataProcessor/{Name}/DataProcessor.obj.bsl` | metadata_object | object | 101 (17/2) | 5 (0/0) | 74 (13/2) | нет | ignored | Конфигуратор A, совпадение текста |
| `Document/{Name}/Document.obj.bsl` | metadata_object | object | 185 (1/0) | 9 (0/0) | 163 (2/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `ExchangePlan/{Name}/ExchangePlan.obj.bsl` | metadata_object | object | 14 (4/0) | — | 8 (1/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Report/{Name}/Report.obj.bsl` | metadata_object | object | 135 (2/0) | 3 (0/0) | 480 (4/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Task/{Name}/Task.obj.bsl` | metadata_object | object | 1 (0/0) | — | — | нет | ignored | Конфигуратор A, совпадение текста |
| `AccountingRegister/{Name}/AccountingRegister.obj.bsl` | metadata_object | record_set | — | — | 1 (0/0) | нет | ignored | upstream `ext_code` + аналогия layout |
| `AccumulationRegister/{Name}/AccumulationRegister.obj.bsl` | metadata_object | record_set | 59 (2/0) | — | 48 (20/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `InformationRegister/{Name}/InformationRegister.obj.bsl` | metadata_object | record_set | 90 (31/3) | — | 59 (17/4) | нет | ignored | Конфигуратор A, совпадение текста |
| `Sequences/{Name}/Sequences.obj.bsl` | metadata_object | record_set | — | — | 3 (2/1) | нет | ignored | **unresolved** |
| `HTTPService/{Name}/HTTPService.obj.bsl` | metadata_object | service | 1 (0/0) | — | — | нет | ignored | Конфигуратор A, совпадение текста |
| `WebService/{Name}/WebService.obj.bsl` | metadata_object | service | 3 (0/0) | 1 (0/0) | — | нет | ignored | Конфигуратор A, совпадение текста |
| `Constant/{Name}/Constant.obj.bsl` | metadata_object | value_manager | 39 (22/0) | — | 9 (4/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Catalog/{Name}/CatalogCommand/{Name}/CatalogCommand.obj.bsl` | metadata_object_command | command | 20 (0/0) | 2 (0/0) | 8 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `DataProcessor/{Name}/DataProcessorCommand/{Name}/DataProcessorCommand.obj.bsl` | metadata_object_command | command | 40 (0/0) | — | 33 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Document/{Name}/DocumentCommand/{Name}/DocumentCommand.obj.bsl` | metadata_object_command | command | 9 (0/0) | — | 5 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `InformationRegister/{Name}/InformationRegisterCommand/{Name}/InformationRegisterCommand.obj.bsl` | metadata_object_command | command | 6 (0/0) | — | 3 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `Report/{Name}/ReportCommand/{Name}/ReportCommand.obj.bsl` | metadata_object_command | command | 1 (0/0) | — | — | нет | ignored | Конфигуратор A, совпадение текста |
| `CommonCommand/{Name}/CommonCommand.obj.bsl` | common_command | command | 29 (0/0) | 1 (0/0) | 21 (0/0) | нет | ignored | Конфигуратор A, совпадение текста |
| `AccountingRegister/{Name}/AccountingRegisterForm/{Name}/AccountingRegisterForm.obj.bsl` | metadata_object_form | form | — | — | 1 (0/0) | `scan_forms` | supported | layout + `scan_forms` |
| `AccumulationRegister/{Name}/AccumulationRegisterForm/{Name}/AccumulationRegisterForm.obj.bsl` | metadata_object_form | form | 17 (3/0) | — | 35 (2/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `BusinessProcess/{Name}/BusinessProcessForm/{Name}/BusinessProcessForm.obj.bsl` | metadata_object_form | form | 10 (3/0) | — | — | `scan_forms` | supported | layout + `scan_forms` |
| `Catalog/{Name}/CatalogForm/{Name}/CatalogForm.obj.bsl` | metadata_object_form | form | 475 (54/17) | 3 (0/0) | 358 (40/24) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `ChartOfAccounts/{Name}/ChartOfAccountsForm/{Name}/ChartOfAccountsForm.obj.bsl` | metadata_object_form | form | — | — | 3 (0/0) | `scan_forms` | supported | layout + `scan_forms` |
| `ChartOfCalculationTypes/{Name}/ChartOfCalculationTypesForm/{Name}/ChartOfCalculationTypesForm.obj.bsl` | metadata_object_form | form | — | — | 3 (1/0) | `scan_forms` | supported | layout + `scan_forms` |
| `ChartOfCharacteristicType/{Name}/ChartOfCharacteristicTypeForm/{Name}/ChartOfCharacteristicTypeForm.obj.bsl` | metadata_object_form | form | 19 (5/0) | — | 11 (2/0) | `scan_forms` | supported | layout + `scan_forms` |
| `DataProcessor/{Name}/Form/{Name}/Form.obj.bsl` | metadata_object_form | form | 418 (4/0) | 42 (1/0) | 454 (11/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `Document/{Name}/DocumentForm/{Name}/DocumentForm.obj.bsl` | metadata_object_form | form | 694 (64/1) | 9 (0/0) | 480 (1/1) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `DocumentJournal/{Name}/DocumentJournalForm/{Name}/DocumentJournalForm.obj.bsl` | metadata_object_form | form | 9 (0/0) | — | 22 (0/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `Enum/{Name}/EnumForm/{Name}/EnumForm.obj.bsl` | metadata_object_form | form | 4 (0/0) | — | 3 (3/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `ExchangePlan/{Name}/ExchangePlanForm/{Name}/ExchangePlanForm.obj.bsl` | metadata_object_form | form | 34 (0/0) | — | 24 (2/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `FilterCriterion/{Name}/FilterCriterionForm/{Name}/FilterCriterionForm.obj.bsl` | metadata_object_form | form | 4 (0/0) | — | 2 (0/0) | `scan_forms` | supported | layout + `scan_forms` |
| `InformationRegister/{Name}/InformationRegisterForm/{Name}/InformationRegisterForm.obj.bsl` | metadata_object_form | form | 148 (25/0) | 3 (0/0) | 226 (36/1) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `Report/{Name}/ReportForm/{Name}/ReportForm.obj.bsl` | metadata_object_form | form | 166 (0/0) | 1 (0/0) | 1934 (1/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |
| `Task/{Name}/TaskForm/{Name}/TaskForm.obj.bsl` | metadata_object_form | form | 7 (0/0) | — | — | `scan_forms` | supported | layout + `scan_forms` |
| `CommonForm/{Name}/CommonForm.obj.bsl` | common_form | form | 162 (1/0) | — | 113 (0/0) | `scan_forms` | supported | Конфигуратор A, совпадение текста |

Всего обнаружено шаблонов: 54. Суффиксы `.ssn`, descent-части
`*.obj.<n>.bsl` и прочие суффиксы не встретились ни в одной выгрузке.

### Отсутствующие модули по каталогам объектов (A)

У модулей объекта и менеджера отсутствующий модуль определяется разницей между
числом каталогов объектов типа и числом объектов с файлом `<Type>.<suffix>.bsl`
непосредственно в каталоге объекта. Примеры по A: `Catalog` — 142 каталога,
`obj` у 124, `mgr` у 65; `Enum` — 395 каталогов, файл у 24; `Constant` — 183
каталога, файл у 39; `InformationRegister` — 216 каталогов, `obj` у 90, `mgr`
у 83. Полные числа по A/B/C приведены в журналах прогонов и воспроизводятся тем
же обходом.

### Формы без файла модуля

| Выгрузка | `scan_forms`: ссылок | без файла | `scan_common_modules`: записей | без файла |
|---|---:|---:|---:|---:|
| A | 2216 | 49 | 651 | 1 |
| B | 76 | 18 | 4 | 0 |
| C | 3738 | 69 | 242 | 1 |
| X2 | 1 | 1 | 0 | 0 |

У части форм `v8unpack` не создаёт `*.obj.bsl`, а `scan_forms` всё равно
возвращает для них путь к модулю. В A таких путей 49, в B — 18, в C — 69; у
общих модулей — по одному в A и C (как в #151). Формы и общие модули
поддерживаются, но это нужно учесть при объединении в общий индекс.

Формы — единственная группа, где совпадение текста с Конфигуратором неполное:
в A для 1 176 непустых модулей форм пары не нашлось. Все текстовые модули форм
Конфигуратора (`.../Forms/{Name}/Ext/Form/Module.bsl`) при этом нашли пару.
Причина расхождения в #202 не выяснялась. Гипотеза — обычные формы, модуль
которых Конфигуратор хранит внутри бинарного `Form.bin` (см. #150). Статус —
**unresolved**, решение — out of scope для #202.

## Невыявленные кандидаты

| owner_kind | Тип | module_kind | Источник кандидата | Наблюдение |
|---|---|---|---|---|
| extension | ConfigurationExtension | managed_application | upstream ConfigurationExtension.ext_code app | X1/X2: 0 BSL |
| extension | ConfigurationExtension | session | upstream ConfigurationExtension.ext_code ssn | X1/X2: 0 BSL |
| extension | ConfigurationExtension | external_connection | upstream ConfigurationExtension.ext_code con | X1/X2: 0 BSL |
| external_data_processor | ExternalDataProcessor | object | epf artifact not provided | not examined |
| external_data_processor | ExternalDataProcessor | form | epf artifact not provided | not examined |
| external_report | ExternalReport | object | erf artifact not provided | not examined |
| external_report | ExternalReport | form | erf artifact not provided | not examined |
| metadata_object | ChartOfAccounts | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | ChartOfCalculationTypes | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | ChartOfCharacteristicType | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | BusinessProcess | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | Task | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | AccountingRegister | manager | upstream ext_code mgr | A/B/C: 0 files |
| metadata_object | CalculationRegister | record_set | upstream ext_code obj | A/B/C: 0 object dirs |
| metadata_object | CalculationRegister | manager | upstream ext_code mgr | A/B/C: 0 object dirs |
| metadata_object | SettingsStorage | manager | upstream ext_code mgr | A/B/C: 0 object dirs |
| metadata_object | Bot | object | upstream ext_code obj | A/B/C: 0 object dirs |
| metadata_object | ExternalDataSourceCube | object | upstream ext_code obj | A/B/C: 0 object dirs |
| metadata_object | ExternalDataSourceCube | manager | upstream ext_code mgr | A/B/C: 0 object dirs |
| metadata_object_form | * | form_descent_part | upstream descent *.obj.<n>.bsl | A/B/C/X: 0 files |

## Повторяемость

| Выгрузка | Вид | Файлов | `*.bsl` | Прогон 1 (sha256, 16) | Прогон 2 (sha256, 16) | Совпадают |
|---|---|---:|---:|---|---|---|
| A | configuration | 23775 | 3996 | `502f5a44daa8ed14` | `502f5a44daa8ed14` | да |
| B | configuration | 720 | 86 | `376309bd8676c910` | `376309bd8676c910` | да |
| C | configuration | 50353 | 5099 | `a8afc212385fb55a` | `a8afc212385fb55a` | да |
| X1 | extension | 12 | 0 | `fc4c3b6b3565bf6a` | `fc4c3b6b3565bf6a` | да |
| X2 | extension | 11 | 0 | `bd66b45b5fd880df` | `fc4c3b6b3565bf6a` | нет (`files_total`) |

Каждая выгрузка прогонялась дважды подряд; сравнивался SHA-256 канонического
JSON агрегатов. У A, B, C и X1 прогоны совпали. У X2 (свежая распаковка) после
первого прогона появился JSON-артефакт сканера форм, поэтому различается только
`files_total`. Второй прогон X2 дал тот же digest, что X1. Сравнение с
выгрузкой Конфигуратором (A) тоже прогонялось дважды с одинаковым результатом.

## Кандидаты для #203

`module_kind` (закрытый набор):
`command`, `common_module`, `external_connection`, `form`, `managed_application`, `manager`, `object`, `ordinary_application`, `record_set`, `service`, `session`, `value_manager`.

`owner_kind` (закрытый набор):
`common_command`, `common_form`, `common_module`, `configuration`, `extension`, `external_data_processor`, `external_report`, `metadata_object`, `metadata_object_command`, `metadata_object_form`.

Дополнительные поля, которые следуют из данных:

- `metadata_type` — тип владельца (`Catalog`, `Enum`, `Sequences`, ...).
  Без него нельзя однозначно определить `module_kind` по суффиксу `obj`.
- `read_status` — не меньше пяти значений: `ok` (непустой), `empty`,
  `whitespace_only` (либо признак рядом с `empty`), `missing` (файла нет),
  `read_error`. Состояние `whitespace_only` встречается на реальных данных
  (например, 17 форм справочников в A).

## Решения по неподдержанному layout

| Случай | Решение |
|---|---|
| Модули уровня конфигурации, объектов, менеджеров, наборов записей, менеджеров значения, команд, сервисов | local implementation (#204–#207); upstream их выгружает корректно |
| Формы без пары у Конфигуратора | out of scope #202, связь с #150 |
| Шесть пустых модулей объекта обработки без файла v8unpack | out of scope: модуль пустой, BSL-текст не теряется; upstream issue не требуется |
| Модули расширения (`app` / `ssn` / `con`) | not_detected: в доступном расширении нет BSL; проверить при появлении артефакта (#209) |
| Внешние обработки и отчёты | not_detected: артефакты не предоставлены; проверить в #209 |
| Raw-layout (до этапа «Организуем код») | out of scope: исследован только normalized-layout, который использует агент |

## Unresolved

- Семантика `Sequences/{Name}/Sequences.obj.bsl` (в C 3 файла: 2 пустых,
  1 из одних пробелов). Кандидат — модуль набора записей последовательности;
  у A нет файлов для сравнения с Конфигуратором.
- `obj` у `ChartOfAccounts`, `ChartOfCalculationTypes`, `AccountingRegister`
  встречается только в C. Семантика выведена из upstream `ext_code` и
  аналогии с A, но с Конфигуратором не сверена.
- Причина, по которой модулям форм нет пары у Конфигуратора (см. выше).
- Точное имя JSON-артефакта сканера форм подтверждено поиском по коду, но не
  выводом прогона.
