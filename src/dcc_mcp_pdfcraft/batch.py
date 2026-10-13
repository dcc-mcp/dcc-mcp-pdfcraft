"""Reviewed, root-confined native document batches in one PdfCraft process.

This is not the whole upstream tool table. Names are an adapter safety policy;
argument schemas come from the installed host on every call.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .cli_transport import CliError, CliTransport

BATCH_TOOLS = frozenset(
    {
        "doc_create",
        "doc_open",
        "doc_close",
        "doc_save",
        "doc_info",
        "doc_set_info",
        "page_add_image",
        "page_add_text",
        "page_render",
        "edit_undo",
        "edit_redo",
    }
)
MAX_STEPS = 64
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def confined_path(root: Path, value: str) -> Path:
    """Resolve relative paths under root, rejecting symlink/traversal escapes."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CliError("a nonempty file path is required")
    path = Path(value)
    path = (root / path if not path.is_absolute() else path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CliError("file path escapes the configured PDFCRAFT_ROOT") from exc
    if path == root:
        raise CliError("file path must name a file, not PDFCRAFT_ROOT")
    return path


def _json_values(output: str, expected: int) -> list[Any]:
    """Native --script prints concatenated pretty JSON values, one per step."""
    decoder = json.JSONDecoder()
    values = []
    remaining = output.lstrip()
    try:
        while remaining:
            value, end = decoder.raw_decode(remaining)
            values.append(value)
            remaining = remaining[end:].lstrip()
    except json.JSONDecodeError as exc:
        raise CliError("native batch returned invalid JSON; do not automatically retry") from exc
    if len(values) != expected:
        raise CliError(f"native batch returned {len(values)} results for {expected} steps; do not automatically retry")
    return values


def _reject_schema_refs(schema: Any) -> None:
    # Host schemas are self-contained. Refuse future external references rather
    # than letting a validator retrieve network data while validating a batch.
    if isinstance(schema, dict):
        if "$ref" in schema or "$dynamicRef" in schema:
            raise CliError("native tool schema references are unsupported")
        for value in schema.values():
            _reject_schema_refs(value)
    elif isinstance(schema, list):
        for value in schema:
            _reject_schema_refs(value)


def _prepare_steps(transport: CliTransport, steps: Any, root: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise CliError(f"steps must contain between 1 and {MAX_STEPS} entries")
    try:
        serialized = json.dumps(steps, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CliError("batch must contain finite JSON values") from exc
    if len(serialized.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise CliError("batch exceeds the 1 MiB script limit")
    catalog = {tool.get("name"): tool for tool in transport.tools()}
    prepared = []
    outputs: list[Path] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) - {"tool", "args", "out"} or not {"tool", "args"} <= set(step):
            raise CliError(f"step {index + 1} must contain only tool, args, and optional render out")
        name = step["tool"]
        if not isinstance(name, str) or name not in BATCH_TOOLS:
            raise CliError(f"step {index + 1}: tool is outside the reviewed document batch subset")
        schema = catalog.get(name, {}).get("input_schema")
        if not isinstance(schema, dict) or not schema:
            raise CliError(f"{name} is unavailable or has no schema in the installed PdfCraft")
        _reject_schema_refs(schema)
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(step["args"])
        except (SchemaError, ValidationError) as exc:
            raise CliError(f"step {index + 1} ({name}) failed native schema validation: {exc.message}") from exc
        args = dict(step["args"])
        record = {"tool": name, "args": args}
        if "out" in step and name != "page_render":
            raise CliError("only page_render accepts an out path")
        if name == "page_render":
            if "out" not in step:
                raise CliError("page_render requires a new .png out path")
            if args.get("dpi", 96) > 144:
                raise CliError("batch rendering is limited to 144 dpi")
            record["out"] = str(_new_output(root, step["out"], ".png", outputs))
        if name == "doc_save":
            if not args.get("path"):
                raise CliError("doc_save requires a new path; in-place writes are disabled")
            args["path"] = str(_new_output(root, args["path"], ".pdf", outputs))
        else:
            if "path" in args:
                args["path"] = str(_input_path(root, args["path"], outputs))
            if "paths" in args:
                args["paths"] = [str(_input_path(root, path, outputs)) for path in args["paths"]]
        if name == "doc_create":
            if args.get("pages", 1) > 100 or args.get("width", 612) > 2000 or args.get("height", 792) > 2000:
                raise CliError("new documents are limited to 100 pages and 2000-point dimensions")
            if len(args.get("paths", [])) > 100:
                raise CliError("new documents are limited to 100 image inputs")
        prepared.append(record)
    return prepared, outputs


def _new_output(root: Path, value: str, suffix: str, outputs: list[Path]) -> Path:
    path = confined_path(root, value)
    if path.suffix.lower() != suffix:
        raise CliError(f"output must have a {suffix} suffix")
    if path.exists() or path.is_symlink() or path in outputs:
        raise CliError("output already exists or is repeated; overwrites are disabled")
    if not path.parent.is_dir():
        raise CliError("output parent directory must already exist")
    outputs.append(path)
    return path


def _input_path(root: Path, value: str, outputs: list[Path]) -> Path:
    path = confined_path(root, value)
    if path not in outputs:
        if not path.is_file():
            raise CliError("input file does not exist")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise CliError("input exceeds the 64 MiB file limit")
    return path


def _restore_paths(value: Any, paths: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_restore_paths(item, paths) for item in value]
    if isinstance(value, dict):
        return {
            key: paths.get(item, item)
            if key in {"path", "image"} and isinstance(item, str)
            else _restore_paths(item, paths)
            for key, item in value.items()
        }
    return value


def secure_publication_supported() -> bool:
    """Whether Python exposes no-follow descriptor-relative file publication."""
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.link in os.supports_dir_fd
    )


def _directory_fd(path: Path, stack: ExitStack) -> int:
    """Walk an absolute directory path without following any symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError:
        os.close(descriptor)
        raise
    stack.callback(os.close, descriptor)
    return descriptor


def _check_output_directory(root: Path, path: Path, descriptor: int) -> None:
    if confined_path(root, str(path)) != path:
        raise CliError("output parent changed during publication")
    expected, current = os.fstat(descriptor), os.stat(path.parent, follow_symlinks=False)
    if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
        raise CliError("output parent was replaced during publication")


def execute_batch(transport: CliTransport, steps: Any) -> dict[str, Any]:
    """Validate everything, execute once, then publish only new output files.

    Outputs are staged inside the root, then hard-linked without replacement.
    No failed or timed-out native invocation is retried. Publication can partly
    succeed if another process creates a destination concurrently; errors name
    the already-published paths so a caller can reconcile instead of replaying.
    """
    if not transport.root:
        raise CliError("execute_pdfcraft_batch requires a configured PDFCRAFT_ROOT")
    root = Path(transport.root).resolve()
    if not root.is_dir():
        raise CliError("PDFCRAFT_ROOT must be an existing directory")
    prepared, outputs = _prepare_steps(transport, steps, root)
    if outputs and not secure_publication_supported():
        raise CliError("safe batch file publication is unavailable on this platform; no native batch was executed")
    published: list[str] = []
    with ExitStack() as stack:
        try:
            directory_fds = {parent: _directory_fd(parent, stack) for parent in {path.parent for path in outputs}}
        except OSError as exc:
            raise CliError("output directory changed or could not be opened safely; no native batch executed") from exc
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix=".pdfcraft-batch-", dir=root))
        staging = Path(temporary)
        mapping = {str(path): str(staging / str(index) / path.name) for index, path in enumerate(outputs)}
        for target in mapping.values():
            Path(target).parent.mkdir()
        for step in prepared:
            args = step["args"]
            if "path" in args:
                args["path"] = mapping.get(args["path"], args["path"])
            if "paths" in args:
                args["paths"] = [mapping.get(path, path) for path in args["paths"]]
            if "out" in step:
                step["out"] = mapping[step["out"]]
        script = staging / "steps.json"
        script.write_text(json.dumps(prepared, allow_nan=False), encoding="utf-8")
        results = _json_values(transport.run(["run", "--script", str(script)]), len(prepared))
        artifacts = []
        for path in outputs:
            source = Path(mapping[str(path)])
            if not source.is_file() or source.stat().st_size > MAX_OUTPUT_BYTES:
                raise CliError("native output is missing or exceeds the 128 MiB limit; no outputs published")
        try:
            for path in outputs:
                # Check the parent again after native execution in case it changed.
                _check_output_directory(root, path, directory_fds[path.parent])
                source = Path(mapping[str(path)])
                # Hash the staged inode, never a mutable destination path.
                metadata = {
                    "path": str(path),
                    "bytes": source.stat().st_size,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
                os.link(source, path.name, dst_dir_fd=directory_fds[path.parent], follow_symlinks=False)
                published.append(str(path))
                _check_output_directory(root, path, directory_fds[path.parent])
                artifacts.append(metadata)
        except (OSError, CliError) as exc:
            raise CliError(f"output publication failed; do not retry; published outputs: {published}") from exc
        return {"steps": _restore_paths(results, {value: key for key, value in mapping.items()}), "outputs": artifacts}
