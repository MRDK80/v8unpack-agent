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

## Изменение политики

Изменение защиты выполняется отдельной задачей после read-only инвентаризации через GitHub API. После изменения необходимо повторно получить полную конфигурацию, проверить фактические check contexts и выполнить безопасные smoke tests.

Настройки collaborators, permissions, secrets, visibility и merge policy не относятся к обычному изменению branch protection и требуют отдельного решения владельца.
