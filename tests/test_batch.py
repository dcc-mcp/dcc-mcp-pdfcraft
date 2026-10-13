"""Validate the batch boundary without installing a native PdfCraft host."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcc_mcp_pdfcraft import batch
from dcc_mcp_pdfcraft.batch import execute_batch
from dcc_mcp_pdfcraft.cli_transport import CliError

requires_secure_publication = pytest.mark.skipif(
    not batch.secure_publication_supported(), reason="safe directory-relative publication is unavailable"
)

DOC = {"type": "integer", "minimum": 1}
SCHEMAS = {
    "doc_create": {"from": {"enum": ["blank"]}, "pages": DOC, "width": {"type": "number"}},
    "doc_save": {"doc": DOC, "path": {"type": "string", "maxLength": 4096}},
    "doc_open": {"path": {"type": "string"}},
    "doc_info": {"doc": DOC},
    "doc_set_info": {
        "doc": DOC,
        "key": {"type": "string", "pattern": "^[A-Z]"},
        "value": {"type": "string", "maxLength": 12},
    },
    "page_add_text": {"doc": DOC, "page": DOC, "text": {"type": "string"}},
    "page_render": {"doc": DOC, "page": DOC, "dpi": {"type": "number", "minimum": 1, "maximum": 600}},
}
REQUIRED = {
    "doc_create": ["from"],
    "doc_save": ["doc"],
    "doc_open": ["path"],
    "doc_info": ["doc"],
    "doc_set_info": ["doc", "key", "value"],
    "page_add_text": ["doc", "page", "text"],
    "page_render": ["doc", "page"],
}


class BatchTransport:
    def __init__(self, root):
        self.root = str(root) if root is not None else None
        self.calls = []
        self.before_return = None

    def tools(self):
        return [
            {
                "name": name,
                "input_schema": {
                    "type": "object",
                    "properties": props,
                    "required": REQUIRED[name],
                    "additionalProperties": False,
                },
            }
            for name, props in SCHEMAS.items()
        ]

    def run(self, args):
        assert args[:2] == ["run", "--script"]
        steps = json.loads(Path(args[2]).read_text())
        self.calls.append(steps)
        results = []
        for step in steps:
            name, values = step["tool"], step["args"]
            if name == "doc_save":
                Path(values["path"]).write_bytes(b"%PDF-test")
            if name == "page_render":
                Path(step["out"]).write_bytes(b"png-test")
            if name == "doc_open":
                assert Path(values["path"]).is_file()
            results.append({"path": values.get("path"), "image": step.get("out"), "text": values.get("text")})
        if self.before_return:
            self.before_return()
        return "\n".join(json.dumps(value, indent=2) for value in results)


@pytest.fixture
def transport(tmp_path):
    return BatchTransport(tmp_path)


def step(tool, **args):
    return {"tool": tool, "args": args}


@requires_secure_publication
def test_batch_stages_outputs_and_returns_every_result(transport, tmp_path):
    pdf, png = tmp_path / "review.pdf", tmp_path / "review.png"
    steps = [
        step("doc_create", **{"from": "blank"}),
        step("page_add_text", doc=1, page=1, text=str(pdf)),
        step("doc_save", doc=1, path=str(pdf)),
        step("doc_open", path=str(pdf)),
        {**step("page_render", doc=2, page=1), "out": str(png)},
    ]
    report = execute_batch(transport, steps)
    assert len(report["steps"]) == len(steps)
    assert report["steps"][1]["text"] == str(pdf)
    assert report["steps"][2]["path"] == str(pdf)
    assert report["steps"][4]["image"] == str(png)
    assert [item["path"] for item in report["outputs"]] == [str(pdf), str(png)]
    assert all(len(item["sha256"]) == 64 and item["bytes"] > 0 for item in report["outputs"])
    assert Path(transport.calls[0][2]["args"]["path"]).name == "review.pdf"
    assert transport.calls[0][2]["args"]["path"] != str(pdf)
    assert steps[2]["args"]["path"] == str(pdf)
    assert not list(tmp_path.glob(".pdfcraft-batch-*"))


@pytest.mark.parametrize("tool", ["js_run", "sign_id_create", "sign_document", "doc_protect", "run", "execute_code"])
def test_unreviewed_tools_never_execute(transport, tmp_path, tool):
    with pytest.raises(CliError, match="reviewed"):
        execute_batch(transport, [step(tool)])
    assert transport.calls == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "steps,match",
    [
        ([], "between"),
        ([step("doc_info", doc=1)] * 65, "between"),
        ([step("doc_info", doc=True)], "schema"),
        ([step("doc_info", doc=0)], "schema"),
        ([step("doc_info", doc=1, code="print(1)")], "schema"),
        ([step("doc_set_info", doc=1, key="lower", value="x")], "schema"),
        ([step("doc_set_info", doc=1, key="Title", value="x" * 13)], "schema"),
        ([step("doc_create", **{"from": "blank", "pages": 101})], "limited"),
        ([step("doc_create", **{"from": "blank", "width": 2001})], "limited"),
        ([step("doc_create", **{"from": "blank", "width": float("nan")})], "finite"),
        ([step("doc_save", doc=1)], "in-place"),
        ([step("page_render", doc=1, page=1)], "out path"),
        ([{**step("page_render", doc=1, page=1, dpi=145), "out": "a.png"}], "144"),
        ([{**step("doc_info", doc=1), "out": "a.png"}], "only page_render"),
        ([{**step("doc_info", doc=1), "extra": True}], "only tool"),
        ([{"tool": "doc_info", "args": []}], "schema"),
        ([{"tool": "doc_info"}], "only tool"),
        ([step("page_add_text", doc=1, page=1, text="x" * (1024 * 1024))], "1 MiB"),
    ],
)
def test_invalid_batches_are_rejected_before_execution(transport, tmp_path, steps, match):
    with pytest.raises(CliError, match=match):
        execute_batch(transport, steps)
    assert transport.calls == []
    assert not list(tmp_path.iterdir())


def test_requires_explicit_existing_root(tmp_path):
    for root in [None, tmp_path / "absent"]:
        with pytest.raises(CliError, match="ROOT"):
            execute_batch(BatchTransport(root), [step("doc_info", doc=1)])


@pytest.mark.parametrize("path", ["../outside.pdf", "missing/a.pdf", "wrong.png"])
def test_rejects_invalid_output_paths(transport, path):
    with pytest.raises(CliError):
        execute_batch(transport, [step("doc_save", doc=1, path=path)])
    assert transport.calls == []


def test_rejects_existing_and_duplicate_outputs(transport, tmp_path):
    path = tmp_path / "existing.pdf"
    path.write_bytes(b"original")
    with pytest.raises(CliError, match="overwrites"):
        execute_batch(transport, [step("doc_save", doc=1, path=str(path))])
    assert path.read_bytes() == b"original"
    with pytest.raises(CliError, match="repeated"):
        execute_batch(transport, [step("doc_save", doc=1, path="new.pdf")] * 2)
    assert transport.calls == []


def test_rejects_symlink_escape(transport, tmp_path):
    outside = tmp_path.parent / "outside-pdfcraft"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks require privileges on this host")
    with pytest.raises(CliError, match="escapes"):
        execute_batch(transport, [step("doc_save", doc=1, path="link/escape.pdf")])
    assert transport.calls == []


@requires_secure_publication
def test_concurrent_destination_creation_is_not_overwritten(transport, tmp_path):
    output = tmp_path / "new.pdf"
    transport.before_return = lambda: output.write_bytes(b"another writer")
    with pytest.raises(CliError, match="publication failed; do not retry"):
        execute_batch(transport, [step("doc_save", doc=1, path=str(output))])
    assert output.read_bytes() == b"another writer"
    assert len(transport.calls) == 1


@requires_secure_publication
def test_failed_batch_is_not_retried_or_published(transport, tmp_path):
    def fail():
        raise CliError("native timeout")

    transport.before_return = fail
    with pytest.raises(CliError, match="timeout"):
        execute_batch(transport, [step("doc_save", doc=1, path="new.pdf")])
    assert len(transport.calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("output,count", [("{}\n{}", 1), ("{} garbage", 1), ("", 1)])
def test_batch_result_parser_fails_closed(output, count):
    with pytest.raises(CliError, match="do not automatically retry"):
        batch._json_values(output, count)


def test_missing_host_schema_and_external_refs_rejected(transport, monkeypatch):
    monkeypatch.setattr(transport, "tools", lambda: [])
    with pytest.raises(CliError, match="unavailable"):
        execute_batch(transport, [step("doc_info", doc=1)])
    monkeypatch.setattr(
        transport, "tools", lambda: [{"name": "doc_info", "input_schema": {"$ref": "https://invalid/schema"}}]
    )
    with pytest.raises(CliError, match="references"):
        execute_batch(transport, [step("doc_info", doc=1)])


@requires_secure_publication
def test_partial_publication_names_completed_outputs(transport, tmp_path):
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    transport.before_return = lambda: second.write_bytes(b"concurrent")
    with pytest.raises(CliError, match="publication failed") as error:
        execute_batch(transport, [step("doc_save", doc=1, path=str(first)), step("doc_save", doc=1, path=str(second))])
    assert str(first) in str(error.value)
    assert first.read_bytes() == b"%PDF-test"
    assert second.read_bytes() == b"concurrent"
    assert len(transport.calls) == 1


@requires_secure_publication
def test_output_size_limit_precedes_publication(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_OUTPUT_BYTES", 3)
    with pytest.raises(CliError, match="no outputs published"):
        execute_batch(transport, [step("doc_save", doc=1, path="large.pdf")])
    assert not list(tmp_path.iterdir())


def test_input_size_limit_precedes_native_execution(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_INPUT_BYTES", 3)
    path = tmp_path / "large.pdf"
    path.write_bytes(b"1234")
    with pytest.raises(CliError, match="input exceeds"):
        execute_batch(transport, [step("doc_open", path=str(path))])
    assert transport.calls == []


@requires_secure_publication
def test_publication_cannot_follow_a_swapped_parent(transport, tmp_path, monkeypatch):
    import os

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    outside = tmp_path.parent / "outside-race"
    outside.mkdir(exist_ok=True)
    actual_link = os.link

    def swap_then_link(source, target, **kwargs):
        output_dir.rename(tmp_path / "original-output")
        output_dir.symlink_to(outside, target_is_directory=True)
        return actual_link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", swap_then_link)
    monkeypatch.setattr(batch, "secure_publication_supported", lambda: True)
    with pytest.raises(CliError, match="publication failed"):
        execute_batch(transport, [step("doc_save", doc=1, path=str(output_dir / "escaped.pdf"))])
    assert not (outside / "escaped.pdf").exists()
    assert (tmp_path / "original-output" / "escaped.pdf").read_bytes() == b"%PDF-test"


def test_unsupported_publication_fails_before_native_execution(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "secure_publication_supported", lambda: False)
    with pytest.raises(CliError, match="unavailable on this platform"):
        execute_batch(transport, [step("doc_save", doc=1, path="new.pdf")])
    assert transport.calls == []
    assert not list(tmp_path.iterdir())


@requires_secure_publication
def test_publication_detects_replaced_directory_without_symlink(transport, tmp_path, monkeypatch):
    import os

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    actual_link = os.link

    def swap_then_link(source, target, **kwargs):
        output_dir.rename(tmp_path / "original-output")
        output_dir.mkdir()
        return actual_link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", swap_then_link)
    monkeypatch.setattr(batch, "secure_publication_supported", lambda: True)
    with pytest.raises(CliError, match="publication failed"):
        execute_batch(transport, [step("doc_save", doc=1, path=str(output_dir / "moved.pdf"))])
    assert not (output_dir / "moved.pdf").exists()
    assert (tmp_path / "original-output" / "moved.pdf").read_bytes() == b"%PDF-test"
