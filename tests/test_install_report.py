"""Tests for the Install SOP report.

The report is validated against Core's schema both here and in CI, because a
schema that is shipped but never executed is what let the PIP-3990 defect reach
a release. These tests run a *real* report -- one the doctor actually produces
on this machine -- through the validator, rather than a hand-written fixture
that would keep passing after the real payload drifted.

No test asserts an exact upstream or adapter version.
"""

from __future__ import annotations

import json

import pytest

from dcc_mcp_pdfcraft import doctor
from dcc_mcp_pdfcraft.install_report import (
    REPORT_STATUS_FAILED,
    REPORT_STATUS_OK,
    REPORT_STATUS_PARTIAL,
    STEP_STATUS_FAIL,
    STEP_STATUS_PASS,
    STEP_STATUS_SKIP,
    build_report,
    validated_report,
    write_receipt,
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in doctor.ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_real_report_validates_against_core_schema(clean_env):
    """A report the doctor actually produces must satisfy the schema."""
    # validated_report raises when the report violates the contract, so this
    # assertion is the validation.
    report = validated_report()

    assert report["dcc_type"] == "pdfcraft"
    assert report["steps"]


def test_report_schema_version_comes_from_core(clean_env):
    """The schema version is read from Core, never redeclared locally."""
    from dcc_mcp_core import install_sop_report_schema_version

    assert validated_report()["schema_version"] == install_sop_report_schema_version()


def test_report_is_json_serializable(clean_env):
    # The report is written as a receipt and consumed by other tools, so a
    # non-serialisable value would fail at the worst moment.
    assert json.dumps(validated_report())


def test_status_is_derived_from_steps(clean_env):
    """A failing step must produce a failing report, not a declared one."""
    report = validated_report({"checks": {}, "headless_ready": False})
    assert report["status"] == REPORT_STATUS_FAILED


def test_ready_host_produces_ok_status(clean_env):
    report = validated_report(
        {
            "checks": {
                "cli": {"status": "ok", "detail": "found /usr/bin/pdfcraft-cli"},
                "desktop_app": {"status": "ok", "detail": "found pdfcraft"},
                "control_file": {"status": "ok", "detail": "port 8123 and a token"},
            },
            "headless_ready": True,
            "ui_ready": True,
        }
    )

    assert report["status"] == REPORT_STATUS_OK
    assert report["verify"]["directly_usable"] is True
    assert report["next_steps"] == []


def test_optional_skips_produce_partial_not_failure(clean_env):
    """A usable-but-incomplete install is partial, not failed."""
    report = validated_report(
        {
            "checks": {
                "cli": {"status": "ok", "detail": "found pdfcraft-cli"},
                "desktop_app": {"status": "missing", "detail": "not found"},
                "control_file": {"status": "not_configured", "detail": "unset"},
            },
            "headless_ready": True,
        }
    )

    assert report["status"] == REPORT_STATUS_PARTIAL
    assert report["verify"]["directly_usable"] is True


def test_verify_records_the_failed_stage(clean_env):
    report = build_report({"checks": {}, "headless_ready": False})

    assert report["verify"]["failure_stage"] == "locate-pdfcraft-cli"
    assert report["verify"]["failure_reason"]
    assert report["verify"]["directly_usable"] is False


def test_next_step_ids_are_unique(clean_env):
    """Duplicate next-step ids are rejected by the validator."""
    report = build_report({"checks": {}, "headless_ready": False})
    ids = [step["id"] for step in report["next_steps"]]

    assert len(ids) == len(set(ids))


def test_control_file_error_is_a_failing_step(clean_env, monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("nonsense", encoding="utf-8")
    monkeypatch.setenv("PDFCRAFT_CONTROL_FILE", str(bad))

    report = build_report()

    step = next(step for step in report["steps"] if step["id"] == "read-control-file")
    assert step["status"] == STEP_STATUS_FAIL


def test_receipt_is_written_and_still_valid(clean_env, tmp_path):
    destination = tmp_path / "receipt.json"

    write_receipt(destination)

    written = json.loads(destination.read_text(encoding="utf-8"))
    # The file on disk is what other tools consume, so validate that too.
    from dcc_mcp_core import validate_install_sop_report

    validate_install_sop_report(written)


def test_step_statuses_are_drawn_from_the_declared_vocabulary(clean_env):
    """Every step uses an adapter-declared status, so callers can branch on it."""
    allowed = {STEP_STATUS_PASS, STEP_STATUS_FAIL, STEP_STATUS_SKIP}

    assert {step["status"] for step in validated_report()["steps"]} <= allowed
