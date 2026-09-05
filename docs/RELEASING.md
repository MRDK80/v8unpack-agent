# Выпуск v8unpack-agent

Этот документ описывает воспроизводимый выпуск wheel и sdist через GitHub Actions и PyPI Trusted Publishing. Обычный CI остаётся в `.github/workflows/tests.yml`; `.github/workflows/release.yml` отвечает только за release/deployment.

## Текущий blocker

До публикации запрещено заменять VCS-зависимость на `v8unpack>=0.19.1`: проверенный PyPI-дистрибутив 0.19.1 не содержит поддержки `ExternalReport` и `.erf`. Поддержка присутствует в upstream `main` и заявлена для 1.2.11, но соответствующий индексный релиз пока отсутствует. Выпуск отслеживается в `saby-integration/v8unpack#30`.

Пока dependency содержит direct/VCS reference, `scripts/validate_release.py` намеренно завершает release с ненулевым кодом до сборки и получения publishing credential.

После upstream-релиза необходимо установить его wheel из PyPI в чистое окружение, проверить `ExternalReport` и `.erf`, запустить релевантные тесты и только затем указать фактически проверенную минимальную версию в `pyproject.toml`.

## Источник версии

Единственный источник версии — статическое поле `project.version` в `pyproject.toml`. Git-тег не вычисляет версию, а подтверждает её и обязан иметь вид `v<version>`. Например, версии `0.1.0rc1` соответствует тег `v0.1.0rc1`. Несовпадение блокирует workflow до upload.

## Защита публикации

- Production publish запускается только push тега `v*`.
- `workflow_dispatch` предназначен только для TestPyPI.
- Release commit должен находиться в истории `main`.
- Для того же SHA должен существовать успешный запуск `tests.yml`.
- Job `publish` получает только `id-token: write`.
- Постоянные PyPI API tokens не используются.
- Job `publish` скачивает уже проверенные artifacts и не выполняет checkout или сборку.
- Environment `pypi` должен иметь required reviewer и ограничение deployment.
- Для TestPyPI используется отдельный environment `testpypi` и отдельный trusted publisher.
- Для тегов `v*` рекомендуется ruleset, разрешающий создание только maintainer.

## Подготовка release candidate

1. Дождаться публикации upstream dependency и подтвердить её функциональным probe.
2. Заменить VCS dependency на проверенную индексную версию.
3. Установить `project.version = "0.1.0rc1"`.
4. Обновить CHANGELOG и release notes.
5. Собрать wheel и sdist локально.
6. Выполнить `twine check` и install smoke из обоих artifacts.
7. Запустить Ruff, Mypy и полный Pytest.
8. Влить PR в `main` только после зелёного CI.
9. Запустить TestPyPI через `workflow_dispatch` и проверить metadata без смешивания индексов.
10. После отдельного подтверждения создать защищённый тег `v0.1.0rc1`.
11. Одобрить deployment в environment `pypi`.
12. Проверить установку опубликованной версии без локального cache.

## Финальный релиз

После успешного release candidate подготовить отдельное изменение версии на `0.1.0`, повторить все проверки и создать тег `v0.1.0` только на проверенном commit в `main`. GitHub Release создаётся отдельным подтверждённым действием и использует те же wheel и sdist.

## Локальная проверка artifacts

Перед тегом выполнить сборку из чистого дерева:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

Проверяются ровно один wheel и один sdist, корректные Name/Version/Requires-Dist, наличие LICENSE и README, console script `v8unpack-agent-run`, отсутствие тестов, локальных путей, credentials и временных artifacts. Wheel должен иметь tag `py3-none-any`, если состав пакета остаётся платформенно независимым.

Install smoke выполняется для wheel и sdist на Ubuntu и Windows с Python 3.10 и 3.12. Проверяются импорт, CLI help, синтетический запуск и создание post-run report.

## Восстановление после ошибки

PyPI artifact одной версии нельзя заменить. При ошибке необходимо остановить дальнейшие действия, при опасном дефекте пометить релиз yanked и выпустить новую версию. Для ошибки в `0.1.0` следующей обычной версией будет `0.1.1`; удаление и повторная загрузка `0.1.0` запрещены.

README разрешается переключить на утверждение `pip install v8unpack-agent` только после фактической production-публикации и post-publish smoke test.
