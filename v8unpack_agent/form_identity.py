"""Каноническая идентичность обычной формы в выгрузке (issue #226).

Модуль отвечает на один вопрос: **что такое «одна форма»**. До #226 роль
идентификатора играло имя каталога формы, из-за чего одноимённые формы разных
владельцев затирали друг друга ещё на этапе обнаружения.

Идентификатор (``form_id``) — относительный POSIX-путь каталога формы от корня
выгрузки, например ``Catalog/<Объект>/Forms/<ИмяФормы>``. Такой ключ:

* уникален по построению, потому что уникален путь в файловой системе;
* совпадает по смыслу с дедупликацией в :mod:`v8unpack_agent.scan_forms`,
  где записи различаются по каталогу формы;
* даёт одинаковую строку на POSIX и NT, потому что нормализуется в POSIX;
* остаётся обезличенным: корень выгрузки в ключ не входит.

Имя формы (``form_name``) сохраняется отдельным полем и **не** обязано быть
уникальным. Владелец описывается ``owner_kind`` и ``owner_name`` — та же
четвёрка полей, что у ``scan_forms.FormEntry``, чтобы слои можно было сшить
без повторного разбора путей.

Модуль не знает ни о распаковщике, ни об индексе: он только обнаруживает
источники и проверяет корректность ключей.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from v8unpack_agent.form_artifact import FormArtifact

__all__ = [
    "BIN_NAME",
    "CONTAINER_NAME",
    "AmbiguousFormNameError",
    "FormBinSource",
    "FormIdentityError",
    "FormSourceUnpacker",
    "LegacyFormUnpacker",
    "adapt_legacy_unpacker",
    "discover_form_sources",
    "form_id_for_bin",
    "resolve_form_root",
    "select_sources",
    "validate_form_id",
]

#: Имя бинарного файла обычной формы в выгрузке.
BIN_NAME = "Form.bin"

#: Каталог-контейнер форм в выгрузке конфигурации.
CONTAINER_NAME = "Forms"

#: Разделитель путей Windows задаётся кодом, чтобы литерал не попадал в текст.
_BACKSLASH = chr(92)

#: Код диагностики: путь до Form.bin не соответствует ожидаемой конвенции.
WARNING_UNSUPPORTED_LAYOUT = "unsupported_layout"

#: Код диагностики: имя формы неоднозначно, отбор по имени невозможен.
WARNING_AMBIGUOUS_LEGACY_NAME = "ambiguous_legacy_name"


class FormIdentityError(ValueError):
    """Некорректный идентификатор формы.

    Поднимается, когда ``form_id`` абсолютный, содержит ``..`` или разделитель
    Windows, либо пуст. Тихая нормализация запрещена: неверный ключ означает,
    что вызывающий код работает не с той формой.
    """


class AmbiguousFormNameError(FormIdentityError):
    """Имя формы соответствует более чем одному источнику.

    Отбор по имени — легаси-режим. Если имя неоднозначно, выбрать «какую-то»
    форму нельзя: это и есть та тихая потеря, которую устраняет #226.
    """


@dataclass(frozen=True)
class FormBinSource:
    """Источник одной обычной формы: ключ, имя, владелец и путь к ``Form.bin``.

    Attributes
    ----------
    form_id:
        Канонический ключ — относительный POSIX-путь каталога формы.
    form_name:
        Имя формы как в выгрузке. Может повторяться у разных владельцев.
    bin_path:
        Путь к ``Form.bin`` на диске (абсолютный или относительный — как был
        передан корень выгрузки).
    owner_kind:
        Тип объекта-владельца, например ``"Catalog"``. ``None``, если layout
        не позволяет его определить.
    owner_name:
        Имя объекта-владельца. ``None`` для форм вне конвенции.
    container_name:
        Имя каталога-контейнера форм. ``None`` вне конвенции.
    warnings:
        Диагностика по этому источнику. Непустой кортеж означает, что форма
        всё равно обнаружена, но её layout нестандартный.
    """

    form_id: str
    form_name: str
    bin_path: Path
    owner_kind: str | None = None
    owner_name: str | None = None
    container_name: str | None = None
    warnings: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        validate_form_id(self.form_id)
        if not self.form_name:
            raise FormIdentityError(
                "form_name не может быть пустым: источник обязан нести имя формы"
            )


def validate_form_id(form_id: str) -> str:
    """Проверить ключ формы и вернуть его без изменений.

    Отклоняет пустую строку, абсолютный путь, разделитель Windows и любой
    сегмент ``..`` или ``.``.
    """
    if not isinstance(form_id, str) or not form_id:
        raise FormIdentityError("form_id не может быть пустым")
    if _BACKSLASH in form_id:
        raise FormIdentityError(
            "form_id должен использовать POSIX-разделитель: " + repr(form_id)
        )
    candidate = PurePosixPath(form_id)
    if candidate.is_absolute():
        raise FormIdentityError("form_id должен быть относительным: " + form_id)
    for part in candidate.parts:
        if part in ("..", "."):
            raise FormIdentityError(
                "form_id не может содержать переход по дереву: " + form_id
            )
    return form_id


def resolve_form_root(unpacked_root: Path, form_id: str) -> Path:
    """Каталог результата для ключа ``form_id`` внутри ``unpacked_root``.

    Ключ проверяется, затем результат сверяется с корнем: выход за его пределы
    (в том числе через symlink) считается ошибкой.
    """
    validate_form_id(form_id)
    root = Path(unpacked_root)
    candidate = root.joinpath(*PurePosixPath(form_id).parts)
    root_resolved = root.resolve()
    resolved = candidate.resolve()
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise FormIdentityError(
            "каталог формы выходит за пределы корня распаковки: " + form_id
        )
    return candidate


def form_id_for_bin(dump_root: Path, bin_path: Path) -> tuple[str, tuple[str, ...]]:
    """Вычислить ``form_id`` по пути к ``Form.bin``.

    Возвращает пару «ключ, диагностика». Ожидаемая конвенция —
    ``.../Forms/<ИмяФормы>/Ext/Form.bin``; ключом становится путь каталога
    формы. Если конвенция не соблюдена, форма не теряется: ключом становится
    путь родительского каталога, а в диагностику попадает код
    ``unsupported_layout``.
    """
    relative = Path(bin_path).relative_to(Path(dump_root))
    parts = relative.parts
    if CONTAINER_NAME in parts:
        index = len(parts) - 1 - parts[::-1].index(CONTAINER_NAME)
        if index + 1 < len(parts):
            form_parts = parts[: index + 2]
            return "/".join(form_parts), ()
    fallback = parts[:-1] if len(parts) > 1 else parts
    return "/".join(fallback), (WARNING_UNSUPPORTED_LAYOUT,)


def _owner_from_form_id(form_id: str) -> tuple[str | None, str | None, str | None]:
    """Разобрать ключ на тип владельца, имя владельца и контейнер."""
    parts = PurePosixPath(form_id).parts
    if len(parts) >= 4 and parts[-2] == CONTAINER_NAME:
        return parts[-4], parts[-3], parts[-2]
    if len(parts) == 3 and parts[-2] == CONTAINER_NAME:
        return None, parts[-3], parts[-2]
    if len(parts) >= 2 and parts[-2] == CONTAINER_NAME:
        return None, None, parts[-2]
    return None, None, None


def discover_form_sources(dump_root: Path) -> list[FormBinSource]:
    """Найти все ``Form.bin`` в выгрузке и описать их источниками.

    В отличие от устаревшей карты «имя → путь», результат не теряет формы при
    совпадении имён: каждый файл превращается в отдельный
    :class:`FormBinSource`. Порядок детерминирован по ``form_id``.
    """
    root = Path(dump_root)
    if not root.is_dir():
        return []
    sources: list[FormBinSource] = []
    for bin_path in sorted(root.rglob(BIN_NAME)):
        if not bin_path.is_file():
            continue
        form_id, diagnostics = form_id_for_bin(root, bin_path)
        if not form_id:
            continue
        owner_kind, owner_name, container_name = _owner_from_form_id(form_id)
        sources.append(
            FormBinSource(
                form_id=form_id,
                form_name=PurePosixPath(form_id).name,
                bin_path=bin_path,
                owner_kind=owner_kind,
                owner_name=owner_name,
                container_name=container_name,
                warnings=diagnostics,
            )
        )
    sources.sort(key=lambda source: source.form_id)
    return sources


def select_sources(
    sources: Sequence[FormBinSource],
    *,
    form_ids: Iterable[str] | None = None,
    form_names: Iterable[str] | None = None,
) -> list[FormBinSource]:
    """Отобрать источники по каноническим ключам или по легаси-именам.

    ``form_ids`` — канонический отбор. ``form_names`` оставлен для
    совместимости и при неоднозначном имени поднимает
    :class:`AmbiguousFormNameError` вместо произвольного выбора.
    """
    if form_ids is not None and form_names is not None:
        raise FormIdentityError(
            "нельзя одновременно отбирать по form_ids и form_names"
        )
    if form_ids is None and form_names is None:
        return list(sources)

    if form_ids is not None:
        wanted = [validate_form_id(value) for value in form_ids]
        by_id = {source.form_id: source for source in sources}
        return [by_id[value] for value in wanted if value in by_id]

    selected: list[FormBinSource] = []
    for name in form_names or ():
        matches = [source for source in sources if source.form_name == name]
        if len(matches) > 1:
            listed = ", ".join(source.form_id for source in matches)
            raise AmbiguousFormNameError(
                "имя формы "
                + repr(name)
                + " соответствует нескольким источникам ("
                + listed
                + "); используйте form_ids ["
                + WARNING_AMBIGUOUS_LEGACY_NAME
                + "]"
            )
        selected.extend(matches)
    return selected


#: Новый протокол распаковки: источник и корень результата.
FormSourceUnpacker = Callable[[FormBinSource, Path], "FormArtifact"]

#: Устаревший протокол: (bin_path, unpacked_root, form_name).
LegacyFormUnpacker = Callable[[Path, Path, str], "FormArtifact"]


def adapt_legacy_unpacker(unpacker: LegacyFormUnpacker) -> FormSourceUnpacker:
    """Обернуть устаревший распаковщик в новый протокол.

    Адаптер существует только для совместимости и предупреждает об этом.
    Имя формы для legacy-вызова берётся из ``form_id``, поэтому каталоги
    одноимённых форм всё равно могут совпасть — уникальность гарантирует
    только новый протокол.
    """
    warnings.warn(
        "протокол (bin_path, unpacked_root, form_name) устарел; перейдите на "
        "(FormBinSource, unpacked_root)",
        DeprecationWarning,
        stacklevel=2,
    )

    def adapted(source: FormBinSource, unpacked_root: Path) -> FormArtifact:
        return unpacker(source.bin_path, unpacked_root, source.form_name)

    return adapted
