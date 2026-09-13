# Защита веток

## Модель ветвления

Разработка ведётся по маршруту `task branch -> main`. Постоянные integration- и roadmap-ветки не используются, поэтому обязательная защита применяется только к `main`.

## Политика `main`

Для `main` действует classic branch protection:

- изменения принимаются только через pull request;
- обязательное число approvals равно нулю, поскольку у репозитория один maintainer; при появлении независимых reviewers это решение следует пересмотреть;
- обсуждения pull request должны быть разрешены;
- head-ветка должна быть актуальна относительно `main`;
- обязательна линейная история;
- правила применяются к администратору репозитория;
- force push и удаление ветки запрещены;
- обязательные подписи коммитов не включены;
- repository rulesets не используются параллельно с classic branch protection.

Обязательные CI checks:

- `pytest / py3.10 / ubuntu-latest`;
- `pytest / py3.10 / windows-latest`;
- `pytest / py3.12 / ubuntu-latest`;
- `pytest / py3.12 / windows-latest`;
- `lint / ruff + mypy / ubuntu-latest`;
- `lint / ruff + mypy / windows-latest`.

## Внешние условные checks

Помимо шести обязательных jobs, определённых в `.github/workflows/tests.yml`,
на push в дефолтную ветку может появляться дополнительный check run
`update-pip-graph`. Его источник — автоматическая отправка зависимостей
(automatic dependency submission) для pip, включаемая в настройках
репозитория в области dependency graph. Этот check создаётся собственным
workflow GitHub, а не файлом в `.github/workflows/`.

Наблюдаемое поведение:

- появляется на push в дефолтную ветку, когда коммит изменяет манифест
  зависимостей (`pyproject.toml`);
- в task CI pull request не наблюдался: PR #270 показал ровно шесть checks;
- после merge PR #270 (`dff3b73d`, манифест изменён) на `main` наблюдалось
  семь check runs, седьмой — `update-pip-graph`, `completed/success`;
- после merge PR #269 (`411d82df`) и PR #273 (`efe4cbdf`), где манифест
  не менялся, наблюдалось шесть check runs.

`update-pip-graph` не входит в список обязательных checks и не должен
добавляться в required status checks: check условный, поэтому pull request
без изменения манифеста будет ожидать проверку, которая не запустится.

### Решение владельца

Automatic dependency submission для pip оставлена включённой осознанно.

Обоснование:

- механизм формирует dependency graph, что полезно для единственной
  runtime-зависимости `v8unpack`, устанавливаемой напрямую из git-ветки
  upstream до публикации релиза (#149);
- запуск условный — только при изменении манифеста в дефолтной ветке,
  поэтому расход минут Actions считается приемлемым;
- список required status checks при этом не изменяется.

### Критерий готовности CI

Готовность определяется по именам обязательных checks, а не по общему числу
check runs:

- все шесть обязательных checks присутствуют по точным именам и имеют
  `status=completed` и `conclusion=success`;
- дополнительные внешние check runs учитываются отдельно и сами по себе
  не делают состояние неуспешным;
- внешний check с `conclusion=failure` фиксируется как отдельный сигнал
  и не меняет список required status checks без решения владельца;
- `queued`, `pending`, `in_progress`, `cancelled`, `timed_out`,
  `action_required`, `skipped` и отсутствующий обязательный check
  успехом не являются.

## Изменение политики

Изменение защиты выполняется отдельной задачей после read-only инвентаризации через GitHub API. После изменения необходимо повторно получить полную конфигурацию, проверить фактические check contexts и выполнить безопасные smoke tests.

Настройки collaborators, permissions, secrets, visibility и merge policy не относятся к обычному изменению branch protection и требуют отдельного решения владельца.
