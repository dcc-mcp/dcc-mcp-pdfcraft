"""Route skill actions to PdfCraft's headless CLI or its control channel.

Every action declared in the shipped skill catalog lands here. The routing is
deliberately thin: the adapter owns process lifecycle, argument encoding and
error normalization, while the command set itself stays owned by upstream and
is discovered at runtime. That split is what keeps this adapter from becoming a
second, drifting copy of PdfCraft's tool table.

Actions that only read or write files run through ``pdfcraft-cli``, which needs
no window and therefore works in CI. Actions that need the live UI are forwarded
to the running desktop app's authenticated control channel and fail with an
actionable message when no instance is attached, rather than silently doing
nothing.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .cli_transport import CliError, CliNotFound, CliTransport
from .control_channel import ControlChannelUnavailable
from .env import ENV_CONTROL_FILE, cli_path, root

# Actions that drive the running desktop application rather than a headless
# session. Everything else runs through the CLI.
UI_ACTIONS = frozenset({"ui_screenshot", "ui_click", "ui_inspect", "ui_elements", "ui_state"})

# Control-channel methods, keyed by action name. The names are PdfCraft's, from
# ``docs/control-protocol.md``.
UI_METHODS = {
    "ui_screenshot": "ui.screenshot",
    "ui_click": "ui.click",
    "ui_inspect": "ui.inspect",
    "ui_elements": "ui.elements",
    "ui_state": "ui.state",
}


active_server: ContextVar[Any] = ContextVar("pdfcraft_active_server", default=None)


class ActionError(RuntimeError):
    """An action could not be completed against the host."""


def _transport() -> CliTransport:
    """Build a CLI transport from the configured (or discovered) binary."""
    instance = active_server.get()
    if instance is not None:
        return CliTransport(instance.cli_binary, root=instance.root)
    return CliTransport(cli_path(), root=root())


def _server():
    """Return the running server, which owns the control channel."""
    from . import server

    instance = active_server.get() or server._server
    if instance is None:  # noqa: SLF001 - module-global access is the lifecycle contract
        raise ControlChannelUnavailable("no PdfCraft server is running")
    return instance


def dispatch(action_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one action and return a structured result envelope."""
    params = dict(params or {})
    if action_name in UI_ACTIONS:
        return _dispatch_ui(action_name, params)
    return _dispatch_cli(action_name, params)


def _dispatch_ui(action_name: str, params: dict[str, Any]) -> dict[str, Any]:
    method = UI_METHODS[action_name]
    try:
        channel = _server().control_channel()
    except ControlChannelUnavailable as exc:
        raise ActionError(
            f"{action_name} needs the running PdfCraft desktop app; {exc}. "
            f"Start it with `pdfcraft --control <file>` and set {ENV_CONTROL_FILE}."
        ) from exc
    try:
        result = channel.call(method, params)
    except ControlChannelUnavailable as exc:
        # The channel is rebuilt per call and the port is read from the control
        # file each time, so there is no stale state to clear here: report the
        # failure as-is.
        raise ActionError(str(exc)) from exc
    return {"action": action_name, "method": method, "result": result}


def _dispatch_cli(action_name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        transport = _transport()
    except CliNotFound as exc:
        raise ActionError(str(exc)) from exc

    try:
        if action_name == "pdf_info":
            path = params.get("path")
            if not path:
                raise ActionError("pdf_info requires a `path` argument")
            return {"action": action_name, "result": transport.info(path)}

        if action_name in {"list_tools", "list_pdfcraft_tools"}:
            return {"action": action_name, "tools": transport.tools()}

        if action_name in {"list_commands", "list_pdfcraft_commands"}:
            return {"action": action_name, "commands": transport.commands()}

        if action_name == "execute_pdfcraft_batch":
            from .batch import execute_batch

            if set(params) != {"steps"}:
                raise ActionError("execute_pdfcraft_batch requires only a steps argument")
            return {"action": action_name, "result": execute_batch(transport, params["steps"])}

        raise ActionError(f"Unsupported adapter action: {action_name}; use the reviewed batch workflow")
    except CliError as exc:
        raise ActionError(str(exc)) from exc
