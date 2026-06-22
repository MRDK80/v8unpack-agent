"""Результат распаковки одной обычной формы.

``extraction_ok=False`` — это **не ошибка пайплайна**, а сигнал агенту: «по
этой форме видна только часть, не делай выводов о полноте». Без такого флага
агент будет молча работать на неполной информации.

``skd_extracted=True`` означает, что для внешнего отчёта (.erf) был успешно
выполнен второй шаг — извлечение запросов СКД в ``skd_queries.json``.
При ``skd_extracted=False`` агент видит только BSL-код модуля.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from v8unpack_agent.form_paths import form_paths


@dataclass(frozen=True)
class FormArtifact:
    """Распакованная форма как артефакт пайплайна.

    Attributes
    ----------
    name:
        Имя формы (как в выгрузке), например ``"ФормаЭлемента"``.
    paths:
        Словарь путей к текстам формы (см. :func:`form_paths`):
        ``object_module``, ``ext_module``, ``metadata``.
    extraction_ok:
        ``True`` — распаковка полная; ``False`` — частичная (вложенные панели,
        нестандартные элементы, артефакты совместимости). При ``False`` поле
        ``extraction_warnings`` обязано быть непустым.
    extraction_warnings:
        Диагностические сообщения о неполноте распаковки. Пустой список при
        ``extraction_ok=True``.
    skd_extracted:
        Только для внешних отчётов (.erf). ``True`` — запросы схемы компоновки
        данных успешно извлечены в ``skd_queries.json`` рядом с распакованной
        директорией. ``False`` — агент видит только BSL модуля, СКД недоступна.
        Для обычных форм и внешних обработок (.epf) поле не актуально (False).
    """

    name: str
    paths: dict[str, Path]
    extraction_ok: bool
    extraction_warnings: list[str] = field(default_factory=list)
    skd_extracted: bool = False

    def __post_init__(self) -> None:
        if not self.extraction_ok and not self.extraction_warnings:
            raise ValueError(
                f"FormArtifact({self.name!r}) с extraction_ok=False обязан "
                "нести хотя бы одно предупреждение в extraction_warnings. "
                "Тихая частичная распаковка запрещена."
            )

    @classmethod
    def for_form(
        cls,
        unpacked_root: Path,
        form_name: str,
        *,
        extraction_ok: bool = True,
        extraction_warnings: list[str] | None = None,
        skd_extracted: bool = False,
    ) -> "FormArtifact":
        """Собрать артефакт по конвенции путей для формы ``form_name``."""
        return cls(
            name=form_name,
            paths=form_paths(unpacked_root, form_name),
            extraction_ok=extraction_ok,
            extraction_warnings=list(extraction_warnings or []),
            skd_extracted=skd_extracted,
        )
