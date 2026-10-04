# Итоговая матрица BSL-покрытия (issue #209)

Документ закрывает эпик #201: для каждого вида BSL-модуля из исследования
#202 ([`research/bsl_module_inventory_issue202.md`](research/bsl_module_inventory_issue202.md))
указаны вид владельца, layout, сканер, тесты, статус в runner и post-run
report и ограничения. Остаток, который агент не обрабатывает, перечислен
явно с причиной и решением. Покрытие заявляется только для normalized-layout
`v8unpack` конфигурации; другие layout в него не входят.

Контракт записей — [`modules.md`](modules.md) (#203). Индекс модулей
включается флагом `--include-module-index` (`RunOptions.include_module_index`,
[`runner.md`](runner.md)); таблица статусов — `summary.modules`
([`run_report.md`](run_report.md)).

## Матрица поддержанных видов

`{Name}` — имя владельца, `{Cmd}` — имя команды, `{Form}` — имя формы.

| `module_kind` | `owner_kind` | Layout (относительный путь) | Сканер | Тесты | Объект отчёта | Ограничения |
|---|---|---|---|---|---|---|
| `ordinary_application` | `configuration` | `Configuration.802.bsl` | `scan_configuration_modules` (#204) | `test_configuration_modules_issue204.py`, `test_bsl_coverage_matrix_issue209.py` | `module_ordinary_application` | файла может не быть (выгрузка B в #202): `missing` |
| `managed_application` | `configuration` | `Configuration.app.bsl` | `scan_configuration_modules` (#204) | те же | `module_managed_application` | — |
| `external_connection` | `configuration` | `Configuration.con.bsl` | `scan_configuration_modules` (#204) | те же | `module_external_connection` | — |
| `session` | `configuration` | `Configuration.seance.bsl` | `scan_configuration_modules` (#204) | те же | `module_session` | — |
| `object` | `metadata_object` | `{Type}/{Name}/{Type}.obj.bsl` для `BusinessProcess`, `Catalog`, `ChartOfCharacteristicType`, `DataProcessor`, `Document`, `ExchangePlan`, `Report`, `Task` | `scan_metadata_object_modules` (#205) | `test_metadata_modules_issue205.py`, `test_bsl_coverage_matrix_issue209.py` | `module_object` | только пары, доказанные в #202 |
| `manager` | `metadata_object` | `{Type}/{Name}/{Type}.mgr.bsl` для `AccumulationRegister`, `Catalog`, `DataProcessor`, `Document`, `DocumentJournal`, `ExchangePlan`, `InformationRegister`, `Report`; `Enum/{Name}/Enum.obj.bsl` | `scan_metadata_object_modules` (#205) | те же | `module_manager` | `Enum.obj.bsl` — модуль менеджера |
| `record_set` | `metadata_object` | `AccumulationRegister/{Name}/AccumulationRegister.obj.bsl`, `InformationRegister/{Name}/InformationRegister.obj.bsl` | `scan_record_set_modules` (#206) | `test_record_set_modules_issue206.py`, `test_bsl_coverage_matrix_issue209.py` | `module_record_set` | разбивка по `metadata_type` в `summary.modules` |
| `command` | `common_command` | `CommonCommand/{Cmd}/CommonCommand.obj.bsl` | `scan_command_modules` (#207) | `test_command_modules_issue207.py`, `test_bsl_coverage_matrix_issue209.py` | `module_command` | — |
| `command` | `metadata_object_command` | `{Type}/{Name}/{Type}Command/{Cmd}/{Type}Command.obj.bsl` для `Catalog`, `DataProcessor`, `Document`, `InformationRegister`, `Report` | `scan_command_modules` (#207) | те же | `module_command` | команды других типов в #202 не встретились |
| `value_manager` | `metadata_object` | `Constant/{Name}/Constant.obj.bsl` | `scan_value_manager_modules` (#337) | `test_value_manager_modules_issue337.py`, `test_bsl_coverage_matrix_issue209.py` | `module_value_manager` | — |
| `service` | `metadata_object` | `HTTPService/{Name}/HTTPService.obj.bsl`, `WebService/{Name}/WebService.obj.bsl` | `scan_service_modules` (#337) | `test_service_modules_issue337.py`, `test_bsl_coverage_matrix_issue209.py` | `module_service` | HTTP-сервис наблюдался только в одной выгрузке |
| `common_module` | `common_module` | `CommonModule/{Name}/CommonModule.obj.bsl` | `scan_common_modules` (#151) | `test_common_modules_issue151.py`, `test_module_index_mixed_issue208.py` | `common_module` (отдельная стадия) | в индекс модулей не входит; прежние статусы `common_modules` |
| `form` | `metadata_object_form`, `common_form` | `{Type}/{Name}/{Type}Form/{Form}/{Type}Form.obj.bsl`, `DataProcessor/{Name}/Form/{Form}/Form.obj.bsl`, `CommonForm/{Form}/CommonForm.obj.bsl` | `scan_forms` (form-pipeline) | `test_scan_forms.py`, `test_module_index_mixed_issue208.py`, `test_bsl_coverage_matrix_issue209.py` | `form` (отдельная стадия) | в индекс модулей не входит; часть форм без файла модуля (elem-only) |

Позитивный и граничный тест есть для каждого вида: профильные тесты
сканеров (#204–#207, #337) и сквозной fixture
`tests/test_bsl_coverage_matrix_issue209.py`, где каждый доказанный layout
даёт `ok`, а граничные владельцы — `empty`, `whitespace_only`, `missing` и
`read_error`. Строгий регистр имён проверяет
`tests/test_exact_case_names_issue208.py`.

## Статусы в runner и post-run report

| Статус чтения | Статус объекта | `reason_code` | Влияние на прогон |
|---|---|---|---|
| `ok` | `complete` | — | нет |
| `empty` | `excluded` | `empty` | нет, код возврата не меняется |
| `whitespace_only` | `excluded` | `whitespace_only` | нет, код возврата не меняется |
| `missing` | `excluded` | `missing` | нет, код возврата не меняется |
| `read_error` | `failed` | `read_error` | degraded, код 3 |

missing — файл модуля отсутствует в выгрузке. Это не доказывает отсутствие
модуля и не считается дефектом выгрузки.

Пороги вида «слишком много missing» не вводятся. Высокая доля `missing` у
`manager`, `value_manager` и `record_set` объясняется layout: #202 показал,
что `v8unpack` пишет файл только у объектов, где модуль есть, а отсутствующие
файлы не теряют непустой BSL-текст. Ошибок чтения на контрольных выгрузках
нет, поэтому порог не имел бы доказательной основы.

Код возврата 3 на реальной выгрузке может возникать и без модулей: его дают
формы (`partial`), общие модули и артефакты СКД. Сравнивать покрытие нужно по
`summary` и `objects` отчёта, а не по коду возврата. Групповые флаги
`--module-group` / `--skip-module-group` (#346) сужают `summary.modules` до
выбранных групп, поэтому для полного замера не используются.

## Регистр имён

Сканеры индекса модулей ищут каталоги типов, контейнеры команд и файлы
модулей по точным каноничным именам; сравнение побайтовое по
`os.scandir()` и одинаково на Linux и Windows (#208, раздел «Регистр имён в
выгрузке» в [`modules.md`](modules.md)). Каталог с неточным регистром не
находится, файл с неточным регистром даёт `missing`. Уникальность
`module_id` и `relative_path` в `ModuleIndex` проверяется без учёта
регистра.

## Замер на трёх выгрузках

Замер выполнен владельцем 4 октября 2026 на SHA
`9bc5c261f66dbb75126e610dbfc3664113a00884` эпик-ветки: runner с
`include_module_index=True` без групповых флагов, по два прогона на свежей
копии каждой выгрузки (сканер форм пишет JSON в каталоги форм, поэтому
реальная выгрузка только читалась). E1, E2, E3 — те же выгрузки, что A, B,
C в #202: совпадает число общих модулей (651 / 4 / 242) и форм с файлом
модуля.

| Проверка | E1 | E2 | E3 |
|---|---:|---:|---:|
| записей индекса модулей | 2550 | 156 | 2816 |
| `ModuleIndex` = `summary.modules` = объекты отчёта | да | да | да |
| модули, учтённые и как форма или общий модуль | 0 | 0 | 0 |
| `read_error` модулей | 0 | 0 | 0 |
| два прогона дали идентичный отчёт (без времени) | да | да | да |
| реальная выгрузка не изменилась | да | да | да |
| BSL-файлов без обработчика | 0 | 0 | 6 |

`summary.modules.by_kind`, формат `ok / empty / whitespace_only / missing / read_error`:

| `module_kind` | E1 | E2 | E3 |
|---|---|---|---|
| `command` | 105 / 0 / 0 / 0 / 0 | 3 / 0 / 0 / 0 / 0 | 70 / 0 / 0 / 0 / 0 |
| `external_connection` | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 |
| `managed_application` | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 |
| `manager` | 274 / 34 / 4 / 1016 / 0 | 0 / 0 / 0 / 74 / 0 | 119 / 55 / 2 / 1312 / 0 |
| `object` | 505 / 58 / 3 / 82 / 0 | 17 / 0 / 0 / 41 / 0 | 761 / 54 / 2 / 63 / 0 |
| `ordinary_application` | 1 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 1 / 0 | 1 / 0 / 0 / 0 / 0 |
| `record_set` | 113 / 33 / 3 / 129 / 0 | 0 / 0 / 0 / 12 / 0 | 66 / 37 / 4 / 172 / 0 |
| `service` | 4 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 | — |
| `session` | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 / 0 |
| `value_manager` | 17 / 22 / 0 / 144 / 0 | 0 / 0 / 0 / 4 / 0 | 5 / 4 / 0 / 86 / 0 |

Распределение BSL-файлов выгрузки по обработчикам: E1 — индекс модулей
1179, общие модули 650, формы 2167; E2 — 24, 4, 58; E3 — 1183, 241, 3669 и
6 без обработчика.

Дополнительно просмотрены выгрузка расширения и каталог распакованных
внешних обработок и отчётов (только чтение): в расширении BSL-файлов нет; во
внешних — 20 BSL-файлов: 6 модулей объекта внешней обработки и 14 модулей
форм.

## Остаток: необработанные BSL-файлы

| Шаблон | Где встретился | Причина | Решение |
|---|---|---|---|
| `Sequences/{Name}/Sequences.obj.bsl` | E3: 3 | семантика в #202 unresolved | вне индекса до сверки с выгрузкой Конфигуратором |
| `ChartOfAccounts/{Name}/ChartOfAccounts.obj.bsl` | E3: 1 | в #202 только upstream `ext_code` и аналогия layout | вне индекса до сверки с Конфигуратором |
| `ChartOfCalculationTypes/{Name}/ChartOfCalculationTypes.obj.bsl` | E3: 1 | то же | вне индекса до сверки с Конфигуратором |
| `AccountingRegister/{Name}/AccountingRegister.obj.bsl` | E3: 1 | то же | вне индекса до сверки с Конфигуратором |
| `{Name}/ExternalDataProcessor.obj.bsl` | внешние обработки: 6 | layout внешних обработок не входит в эпик #201, семантика с Конфигуратором не сверена | новый вид для индекса; follow-up issue по решению владельца |
| модули форм внешних обработок и отчётов | внешние: 14 | обрабатываются `scan_forms` в режиме `mode="external"`, а не индексом модулей | без изменений |
| модули расширения (`ConfigurationExtension`) | расширение: 0 файлов | артефакта с BSL нет | not_detected; проверить при появлении артефакта |
| raw-layout и выгрузка Конфигуратором в файлы | — | исследован только normalized-layout | вне scope |

Unresolved-случаи не классифицируются догадкой и не дают записей, в том
числе `missing`. На выгрузке расширения сканер объектов и менеджеров
создаёт `missing` для заимствованных объектов без модулей (2 + 2): это
следствие общего правила missing, а не поддержка расширений.
