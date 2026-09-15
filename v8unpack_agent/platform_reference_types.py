"""Статическая таблица платформенных типов: UUID → каноническое имя XDTO.

Вторая ступень резолюции ссылочных типов (issue #165). Первая ступень —
:attr:`v8unpack_agent.scan_forms.FormScanIndex.reference_types`, построенная
обходом выгрузки; последняя — fallback ``Ref#uuid`` в
:func:`v8unpack_agent.object_decoder.decode_object_attributes` (issue #88).

Происхождение данных
--------------------
Платформенная природа идентификаторов доказана в issue #164 (три независимые
конфигурации, ``in_index_but_unresolved = 0``). Канонические машинные имена
получены контролируемым экспериментом и подтверждены двумя независимыми
каналами — токеном ``v8:Type`` / ``v8:TypeSet`` в выгрузке XML и фабрикой
XDTO: отчёт ``docs/research/platform_type_names_issue165.md`` (PR #275, PR
#276).

Правила таблицы
---------------
* значение — стабильное машинное имя XDTO в префиксной форме
  ``prefix:LocalName``; локальные имена конфигурации и локализованные подписи
  интерфейса сюда не попадают;
* таблица заполняется вручную, без автогенерации и без чтения выгрузки;
* UUID без публикуемого имени вынесен в
  :data:`PLATFORM_TYPES_WITHOUT_XDTO_NAME` и в резолюции не участвует;
* инвариант ``13 + 1 = 14`` при пустом пересечении множеств;
* таблица не сериализуется в составе ``FormScanIndex``; schema v2 неизменна.

Префиксы пространств имён (журнал прогонов, отчёт #165)::

    v8           http://v8.1c.ru/8.1/data/core
    cfg          http://v8.1c.ru/8.1/data/enterprise/current-config
    enterprise   http://v8.1c.ru/8.1/data/enterprise
    dcsset       http://v8.1c.ru/8.1/data-composition-system/settings
"""

from __future__ import annotations

PLATFORM_REFERENCE_TYPES: dict[str, str] = {
    "acf6192e-81ca-46ef-93a6-5a6968b78663": "v8:ValueTable",
    "4772b3b4-f4a3-49c0-a1a5-8cb5961511a3": "v8:ValueListType",
    "fc01b5df-97fe-449b-83d4-218a090e681e": "v8:UUID",
    "2fdc88ec-7c9b-43cd-8ba5-873f043bdd88": "v8:StandardPeriod",
    "e603c0f2-92fb-4d47-8f38-a44a381cf235": "v8:ValueTree",
    "e199ca70-93cf-46ce-a54b-6edc88c3a296": "v8:ValueStorage",
    "280f5f0e-9c8a-49cc-bf6d-4d296cc17a63": "cfg:AnyIBRef",
    "38bfd075-3e63-4aaa-a93e-94521380d579": "cfg:DocumentRef",
    "e61ef7b8-f3e1-4f4b-8ac7-676e90524997": "cfg:CatalogRef",
    "0a52f9de-73ea-4507-81e8-66217bead73a": "cfg:ExchangePlanRef",
    "474c3bf6-08b5-4ddc-a2ad-989cedf11583": "cfg:EnumRef",
    "cab0d12b-3c88-4993-8edc-8c3827cadc7d": "dcsset:SettingsComposer",
    "b1b064f3-ae38-49bf-8c6d-390c65fd94af": "enterprise:ComparisonType",
}
"""13 доказанных платформенных типов: UUID → каноническое имя XDTO."""

PLATFORM_TYPES_WITHOUT_XDTO_NAME: frozenset[str] = frozenset(
    {
        # Тип доказан изолированным слотом, машинного имени у него нет:
        # сериализатор вернул отсутствие имени при четырёх успешных
        # контрольных вызовах того же цикла (отчёт #165, раздел 4).
        "0dda99d9-ae9f-43d2-b7ac-44f3fb0d4059",
    }
)
"""UUID доказанного объёма без публикуемого имени: остаётся ``Ref#uuid``."""

__all__ = [
    "PLATFORM_REFERENCE_TYPES",
    "PLATFORM_TYPES_WITHOUT_XDTO_NAME",
]
