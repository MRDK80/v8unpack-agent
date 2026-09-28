"""Оставшиеся ограничения диагностической санитизации (issue #301).

Follow-up к #142. Все пути, имена пользователей, хосты и модули
синтетические. Canary-строки собираются из сегментов через ``SEP_NT`` и
``"/"``, а не через ``Path``, поэтому проверки не зависят от разделителя
текущей ОС.

1. Абсолютный ``bsl_path`` общего модуля не переносит компоненты вне
   export root в ``report.objects[].object``.
2. Сегмент абсолютного пути с пробелом не оставляет видимым хвост имени
   пользователя в ``forms_scan_index.json`` и post-run report; относительные
   идентификаторы с пробелом не меняются.
3. Имя пользователя вне ``home``/``Users``/``root``/``~`` — осознанная
   граница, описанная в ``docs/diagnostic_sanitizer.md``; здесь не
   тестируется.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import v8unpack_agent.scan_forms as sf
from v8unpack_agent import runner
from v8unpack_agent._safe_paths import sanitize_diagnostic
from v8unpack_agent.common_modules import CommonModuleEntry, CommonModuleIndex
from v8unpack_agent.run_report import RunObjectKind, write_post_run_report
from v8unpack_agent.scan_forms import SCAN_WARNING_FORM_SCAN_ERROR, scan_warning_code

#: Разделитель NT задан кодом символа: литерала в тексте теста нет.
SEP_NT = chr(92)

MODULE_TAIL = ["CommonModule", "SharedHelpers", "CommonModule.obj.bsl"]
MODULE_ID = "/".join(MODULE_TAIL)
UNKNOWN_OBJECT = "unknown_object"

USER = "canary-user-301"
SPACED_USER = "Canary User-301"
HOST = "canary-host-301"
SHARE = "canary-share-301"
ROOT = "private-root-301"

MODULE_CANARIES = {
    "posix": "/" + "/".join(["home", USER, ROOT, *MODULE_TAIL]),
    "windows": SEP_NT.join(["C:", "Users", USER, ROOT, *MODULE_TAIL]),
    "windows_slash": "/".join(["C:", "Users", USER, ROOT, *MODULE_TAIL]),
    "unc": SEP_NT * 2 + SEP_NT.join([HOST, SHARE, ROOT, *MODULE_TAIL]),
}

FORM_TAIL = ["Catalog", "Obj", "CatalogForm", "Form", "Form.json"]
SPACED_CANARIES = {
    "posix_home": "/" + "/".join(["home", SPACED_USER, ROOT, *FORM_TAIL]),
    "posix_root_dir": "/"
    + "/".join(["srv", "Private Root-301", ROOT, *FORM_TAIL]),
    "windows_users": SEP_NT.join(["C:", "Users", SPACED_USER, ROOT, *FORM_TAIL]),
    "windows_escaped": (SEP_NT * 2).join(
        ["C:", "Users", SPACED_USER, ROOT, *FORM_TAIL]
    ),
    "unc_share": SEP_NT * 2
    + SEP_NT.join([HOST, "Canary Share-301", ROOT, *FORM_TAIL]),
}

LOCAL_MARKERS = (
    USER,
    "User-301",
    "Root-301",
    "Share-301",
    HOST,
    SHARE,
    ROOT,
    "/home/",
    "C:" + SEP_NT,
    "C:/",
    SEP_NT,
)


def _assert_clean(text: str) -> None:
    for marker in LOCAL_MARKERS:
        assert marker not in text, f"маркер {marker!r} найден в {text!r}"


def _raiser(error: BaseException):
    def raise_error(*args: object, **kwargs: object) -> None:
        raise error

    return raise_error


def _canary_error(path: str) -> OSError:
    return OSError(f"[Errno 13] Permission denied: '{path}'")


def _common_module_index(bsl_path: object) -> CommonModuleIndex:
    entry = CommonModuleEntry(name="SharedHelpers", bsl_path=Path(str(bsl_path)))
    return CommonModuleIndex(modules=[entry])


def _module_objects(outcome: runner.RunOutcome) -> list[str]:
    return [
        item.object
        for item in outcome.report.objects
        if item.object_kind is RunObjectKind.COMMON_MODULE
    ]


# --------------------------------------------------------------------------- #
# 1. Абсолютный bsl_path общего модуля
# --------------------------------------------------------------------------- #
def test_real_scanner_keeps_relative_common_module_id(tmp_path: Path) -> None:
    """Реальный scanner даёт относительный bsl_path: идентификатор не меняется."""
    module_dir = tmp_path.joinpath(*MODULE_TAIL[:2])
    module_dir.mkdir(parents=True)
    (module_dir / MODULE_TAIL[2]).write_text("// shared\n", encoding="utf-8")

    outcome = runner.run_pipeline(
        runner.RunOptions(export_root=tmp_path, include_skd=False)
    )

    assert _module_objects(outcome) == [MODULE_ID]


@pytest.mark.parametrize("kind", sorted(MODULE_CANARIES))
def test_absolute_common_module_path_does_not_leak(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = MODULE_CANARIES[kind]
    monkeypatch.setattr(
        runner, "scan_common_modules", lambda root: _common_module_index(canary)
    )

    outcome = runner.run_pipeline(
        runner.RunOptions(export_root=tmp_path, include_skd=False)
    )

    assert _module_objects(outcome) == [MODULE_ID]
    _assert_clean(outcome.report.to_json())

    target = tmp_path / "post-run.json"
    write_post_run_report(outcome.report, target)
    _assert_clean(target.read_text(encoding="utf-8"))


def test_absolute_path_outside_layout_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "/" + "/".join(["home", USER, ROOT, "Module.bsl"])
    monkeypatch.setattr(
        runner, "scan_common_modules", lambda root: _common_module_index(canary)
    )

    outcome = runner.run_pipeline(
        runner.RunOptions(export_root=tmp_path, include_skd=False)
    )

    assert _module_objects(outcome) == [UNKNOWN_OBJECT]
    _assert_clean(outcome.report.to_json())


def test_common_module_object_id_is_deterministic() -> None:
    relative = Path(*MODULE_TAIL)
    assert runner._common_module_object_id(relative) == MODULE_ID
    for canary in MODULE_CANARIES.values():
        first = runner._common_module_object_id(canary)
        assert first == MODULE_ID
        assert runner._common_module_object_id(canary) == first
        assert runner._common_module_object_id(first) == first


# --------------------------------------------------------------------------- #
# 2. Сегмент пути с пробелом
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", sorted(SPACED_CANARIES))
def test_spaced_segment_is_not_left_visible(kind: str) -> None:
    canary = SPACED_CANARIES[kind]
    code = SCAN_WARNING_FORM_SCAN_ERROR
    text = f"[Errno 13] Permission denied: '{canary}' [code={code}]"

    first = sanitize_diagnostic(text)

    _assert_clean(first)
    assert "Form/Form.json" in first
    assert sanitize_diagnostic(first) == first
    assert scan_warning_code(first) == SCAN_WARNING_FORM_SCAN_ERROR


@pytest.mark.parametrize(
    "text",
    [
        "Catalog/Sample Object/ItemForm/Variant1",
        "error scanning Catalog/Sample Object/ItemForm/Variant1: boom",
        "reason: binding_not_proven ./rel/x a/b",
    ],
)
def test_relative_text_with_spaces_is_unchanged(text: str) -> None:
    assert sanitize_diagnostic(text) == text


def test_text_after_path_is_kept() -> None:
    posix = "/" + "/".join(["tmp", "x"])
    assert sanitize_diagnostic(f"read {posix} failed") == "read .../tmp/x failed"
    other = "/" + "/".join(["c", "d"])
    assert (
        sanitize_diagnostic(f"found {posix} and {other}")
        == "found .../tmp/x and .../c/d"
    )


@pytest.mark.parametrize("kind", sorted(SPACED_CANARIES))
def test_spaced_path_in_persisted_index(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = SPACED_CANARIES[kind]
    export_root = tmp_path / "export"
    form_dir = export_root / "Catalog" / "Obj" / "CatalogForm" / "Form"
    form_dir.mkdir(parents=True)
    (form_dir / "CatalogForm.obj.bsl").write_text("// module\n", encoding="utf-8")
    (form_dir / "CatalogForm.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sf, "_scan_form_dir", _raiser(_canary_error(canary)))
    index = sf.scan_forms(export_root)

    saved = tmp_path / "forms_scan_index.json"
    index.save(saved)
    payload = saved.read_text(encoding="utf-8")
    _assert_clean(payload)
    warnings = json.loads(payload)["scan_warnings"]
    prefix = "error scanning Catalog/Obj/CatalogForm/Form: "
    assert any(warning.startswith(prefix) for warning in warnings)


@pytest.mark.parametrize("kind", sorted(SPACED_CANARIES))
def test_spaced_path_in_post_run_report(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = SPACED_CANARIES[kind]
    monkeypatch.setattr(runner, "scan_forms", _raiser(_canary_error(canary)))

    outcome = runner.run_pipeline(runner.RunOptions(export_root=tmp_path))

    fatal = outcome.report.fatal_error
    assert fatal is not None
    assert fatal.reason_code == runner.FATAL_SCAN_FAILED
    _assert_clean(outcome.report.to_json())


def test_relative_form_id_with_space_is_preserved(tmp_path: Path) -> None:
    form_dir = tmp_path / "Catalog" / "Sample Object" / "ItemForm" / "Variant1"
    form_dir.mkdir(parents=True)
    (form_dir / "ItemForm.obj.bsl").write_text("// module\n", encoding="utf-8")

    outcome = runner.run_pipeline(
        runner.RunOptions(
            export_root=tmp_path, include_common_modules=False, include_skd=False
        )
    )

    forms = [
        item.object
        for item in outcome.report.objects
        if item.object_kind is RunObjectKind.FORM
    ]
    assert forms == ["Catalog/Sample Object/ItemForm/Variant1"]
