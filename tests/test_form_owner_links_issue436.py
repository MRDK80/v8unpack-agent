"""Связь владельца для общих и внешних форм по layout выгрузки (issue #436)."""

from __future__ import annotations

import pytest

from v8unpack_agent.search_contract import (
    LINK_BASES,
    ArtifactRef,
    OwnerLink,
    OwnerRef,
)

COMMON = OwnerRef("common_form", "CommonForm", "Выбор")
PROCESSOR = OwnerRef("external_data_processor", "ExternalDataProcessor", "Загрузка")
CATALOG = OwnerRef("metadata_object", "Catalog", "Товары")


def _form(key: tuple[str, str, str, str], owner: OwnerRef | None) -> ArtifactRef:
    return ArtifactRef.form(*key, owner=owner)


COMMON_KEY = ("CommonForm", "", "CommonForm", "Выбор")
PROCESSOR_KEY = ("ExternalDataProcessor", "Загрузка.epf", "Form", "Форма")


def test_layout_is_a_confirmed_basis() -> None:
    assert "layout" in LINK_BASES
    link = OwnerLink(COMMON, _form(COMMON_KEY, COMMON), "layout")
    assert link.confirmed
    assert OwnerLink.from_dict(link.to_dict()) == link
    external = OwnerLink(PROCESSOR, _form(PROCESSOR_KEY, PROCESSOR), "layout")
    assert external.confirmed


@pytest.mark.parametrize(
    ("owner", "key", "artifact_owner"),
    [
        # владелец артефакта не совпадает со связью
        (COMMON, COMMON_KEY, None),
        # общая форма — только сама себе владелец
        (OwnerRef("common_form", "CommonForm", "Другая"), COMMON_KEY,
         OwnerRef("common_form", "CommonForm", "Другая")),
        # внешний владелец другого типа
        (OwnerRef("external_report", "ExternalReport", "Загрузка"), PROCESSOR_KEY,
         OwnerRef("external_report", "ExternalReport", "Загрузка")),
        # layout не определён для форм объектов конфигурации
        (CATALOG, ("Catalog", "Товары", "CatalogForm", "Форма"), CATALOG),
    ],
)
def test_invalid_layout_links_are_rejected(
    owner: OwnerRef, key: tuple[str, str, str, str], artifact_owner: OwnerRef | None
) -> None:
    with pytest.raises(ValueError):
        OwnerLink(owner, _form(key, artifact_owner), "layout")


def test_layout_link_must_target_form() -> None:
    module = ArtifactRef(artifact_kind="module", owner=COMMON, module_kind="form")
    with pytest.raises(ValueError):
        OwnerLink(COMMON, module, "layout")
