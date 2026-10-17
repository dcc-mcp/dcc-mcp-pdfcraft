"""The supported Core line is machine-checkable package metadata."""

from importlib.metadata import requires, version

from dcc_mcp_core.deployment.core_bounds import check_runtime


def test_core_requirement_obeys_one_minor_line_contract():
    requirement = next(item for item in requires("dcc-mcp-pdfcraft") if item.startswith("dcc-mcp-core"))
    assert check_runtime(requirement, version("dcc-mcp-core"))["ok"] is True
    assert check_runtime(requirement, "0.20.44")["ok"] is True
    assert check_runtime(requirement, "0.21.0")["ok"] is False
    assert check_runtime(requirement, "0.20.43")["ok"] is False
