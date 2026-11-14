import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import requires_symlinks
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc.project import get_project_structure, validate_project


def make_project(root: Path, *, project_type: str = "plc-project") -> Path:
    (root / "pous" / "programs").mkdir(parents=True)
    (root / "pous" / "function-blocks").mkdir(parents=True)
    (root / "devices").mkdir()
    (root / "project.json").write_text(
        json.dumps({"meta": {"name": "Example", "type": project_type}}), encoding="utf-8"
    )
    (root / "devices" / "configuration.json").write_text("{}", encoding="utf-8")
    (root / "pous" / "programs" / "main.st").write_text(
        "PROGRAM main\nEND_PROGRAM\n", encoding="utf-8"
    )
    (root / "pous" / "function-blocks" / "Motor.st").write_text(
        "FUNCTION_BLOCK Motor\nEND_FUNCTION_BLOCK\n", encoding="utf-8"
    )
    return root


def test_get_project_structure_uses_current_project_layout(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")

    structure = get_project_structure(str(project))

    assert structure["name"] == "Example"
    assert structure["type"] == "plc-project"
    assert "pous/programs/main.st" in structure["files"]
    assert "pous/function-blocks/Motor.st" in structure["files"]


def test_legacy_json_pou_is_not_in_project_structure(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    legacy_pou = project / "pous" / "function-blocks" / "Legacy.json"
    legacy_pou.write_text("{}", encoding="utf-8")

    assert "pous/function-blocks/Legacy.json" not in get_project_structure(str(project))["files"]


@pytest.mark.parametrize("project_type", ["plc-project", "plc-library"])
def test_accepted_project_types(tmp_path: Path, project_type: str) -> None:
    project = make_project(tmp_path / "project", project_type=project_type)

    result = get_project_structure(str(project))

    assert result["type"] == project_type


def test_legacy_plc_project_type_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project", project_type="PLC")

    with pytest.raises(ToolError, match="unsupported project.json meta.type"):
        get_project_structure(str(project))


def test_a_meta_only_project_is_recognized(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Minimal", "type": "plc-project"}}), encoding="utf-8"
    )

    result = get_project_structure(str(project))

    assert result["name"] == "Minimal"
    assert result["files"] == ["project.json"]


def test_invalid_project_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        get_project_structure(str(tmp_path / "missing"))


def test_project_path_must_be_directory(tmp_path: Path) -> None:
    project_file = tmp_path / "project.json"
    project_file.write_text("{}", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory"):
        get_project_structure(str(project_file))


def test_missing_project_json_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="does not contain project.json"):
        get_project_structure(str(tmp_path))


def test_invalid_project_json_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(ToolError, match="not valid JSON"):
        get_project_structure(str(project))


def test_unsupported_meta_type_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "X", "type": "something-else"}}), encoding="utf-8"
    )

    with pytest.raises(ToolError, match="unsupported project.json meta.type"):
        get_project_structure(str(project))


def test_validate_project_reports_valid_project(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")

    result = validate_project(str(project))

    assert result == {
        "valid": True,
        "name": "Example",
        "type": "plc-project",
        "warnings": [],
    }


def test_validate_project_keeps_meta_only_project_valid(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Minimal", "type": "plc-project"}}), encoding="utf-8"
    )

    result = validate_project(str(project))

    assert result["valid"] is True
    assert result["warnings"] == []


@pytest.mark.parametrize(
    "content",
    [None, "{not-json", json.dumps({"meta": {"name": "X", "type": "bad"}})],
    ids=["missing-project-json", "invalid-json", "bad-type"],
)
def test_validate_project_reports_unrecoverable_failures_as_errors(
    tmp_path: Path, content: str | None
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    if content is not None:
        (project / "project.json").write_text(content, encoding="utf-8")

    with pytest.raises(ToolError):
        validate_project(str(project))


@pytest.mark.parametrize(
    "project_type",
    [None, 5, True, ["plc-project"], {"value": "plc-project"}, ["plc-project", "plc-library"]],
    ids=["null", "integer", "boolean", "list", "object", "list-of-two"],
)
def test_non_string_meta_type_is_rejected_as_domain_error(tmp_path: Path, project_type: object) -> None:
    """meta.type as a list or object previously escaped as a raw TypeError."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "X", "type": project_type}}), encoding="utf-8"
    )

    with pytest.raises(ToolError, match="unsupported project.json meta.type"):
        validate_project(str(project))


@pytest.mark.parametrize("meta", [None, [], "meta"], ids=["null-meta", "list-meta", "string-meta"])
def test_missing_meta_shapes_are_rejected(tmp_path: Path, meta: object) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(json.dumps({"meta": meta}), encoding="utf-8")

    with pytest.raises(ToolError, match="missing meta"):
        validate_project(str(project))


@pytest.mark.parametrize("name", [None, 5, ["X"]], ids=["null-name", "integer-name", "list-name"])
def test_non_string_meta_name_is_rejected(tmp_path: Path, name: object) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": name, "type": "plc-project"}}), encoding="utf-8"
    )

    with pytest.raises(ToolError, match="meta.name must be a string"):
        validate_project(str(project))


@pytest.mark.parametrize("content", ["[]", "null", '"text"', "5"], ids=["array", "null", "string", "number"])
def test_non_object_project_json_is_rejected(tmp_path: Path, content: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(content, encoding="utf-8")

    with pytest.raises(ToolError, match="must contain a JSON object"):
        validate_project(str(project))


def test_non_utf8_project_json_names_the_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_bytes(
        '{"meta": {"name": "Ação", "type": "plc-project"}}'.encode("latin-1")
    )

    with pytest.raises(ToolError, match="Could not read project.json"):
        validate_project(str(project))


def test_utf8_bom_project_json_is_rejected_as_invalid_json(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_bytes(
        b'\xef\xbb\xbf{"meta": {"name": "X", "type": "plc-project"}}'
    )

    with pytest.raises(ToolError, match="project.json is not valid JSON"):
        validate_project(str(project))


def test_unreadable_directory_is_a_tool_error_naming_it(tmp_path: Path, monkeypatch) -> None:
    """A directory that cannot be enumerated is an error, never a silent empty collection."""
    project = make_project(tmp_path / "project")
    real_walk = os.walk

    def walk_reporting_locked_directory(top, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror is not None and str(top).endswith("programs"):
            onerror(PermissionError(13, "Permission denied", str(top)))
        return real_walk(top, **kwargs)

    monkeypatch.setattr(
        "openplc_engineering_mcp.openplc.project.os.walk", walk_reporting_locked_directory
    )

    with pytest.raises(ToolError, match="Could not inspect directory"):
        get_project_structure(str(project))


def test_relative_project_path_resolves_against_the_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path / "project")
    monkeypatch.chdir(tmp_path)

    structure = get_project_structure("project")

    assert structure["path"] == str(project.resolve())


def test_project_paths_with_spaces_and_accents_are_supported(tmp_path: Path) -> None:
    project = make_project(tmp_path / "Projeto Café" / "Linha 1")
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Acentuação", "type": "plc-project"}}), encoding="utf-8"
    )

    result = validate_project(str(project))

    assert result["name"] == "Acentuação"
    assert get_project_structure(str(project))["files"] == [
        "devices/configuration.json",
        "pous/function-blocks/Motor.st",
        "pous/programs/main.st",
        "project.json",
    ]


@requires_symlinks
def test_escaping_symlink_file_is_not_in_project_structure(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    external = tmp_path / "External.st"
    external.write_text("PROGRAM External\nEND_PROGRAM\n", encoding="utf-8")
    (project / "pous" / "programs" / "External.st").symlink_to(external)

    assert "pous/programs/External.st" not in get_project_structure(str(project))["files"]
    assert "pous/programs/main.st" in get_project_structure(str(project))["files"]


@requires_symlinks
def test_symlinked_pou_directory_is_not_descended_in_structure(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    external_dir = tmp_path / "external-pous"
    external_dir.mkdir()
    (external_dir / "Outside.st").write_text("PROGRAM Outside\nEND_PROGRAM\n", encoding="utf-8")
    shutil.rmtree(project / "pous" / "programs")
    (project / "pous" / "programs").symlink_to(external_dir)

    assert get_project_structure(str(project))["files"] == [
        "devices/configuration.json",
        "pous/function-blocks/Motor.st",
        "project.json",
    ]
