"""PdfCraft MCP server composition and lifecycle."""

from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Any, Optional

from dcc_mcp_core import AdapterReadinessBinder, DccServerOptions, HostExecutionBridge
from dcc_mcp_core.host import QueueDispatcher, StandaloneHost
from dcc_mcp_core.server_base import DccServerBase

from .__version__ import __version__
from .actions import register_actions
from .control_channel import ControlChannel, ControlChannelUnavailable, read_endpoint
from .env import ENV_CLI, ENV_CONTROL_FILE, ENV_ROOT
from .host import discover, find_binary

# PdfCraft is driven from an external process, so the adapter owns no port of
# its own by default and lets Core pick one.
DEFAULT_PORT = 0


class PdfCraftMcpServer(DccServerBase):
    """DCC-MCP server that drives PdfCraft as an external process.

    PdfCraft has no embedded Python, so unlike the Maya or Blender adapters this
    server never runs inside the host. It composes two host surfaces instead:

    * a headless ``pdfcraft-cli`` subprocess, which is the primary path and the
      only one available in CI;
    * the desktop app's authenticated loopback control channel, which is used
      when a running instance publishes its ``--control`` file.

    Both are optional at construction time: a machine that only has the CLI
    installed still gets every headless capability, and the absence of the
    desktop app is reported as readiness rather than raised as a startup
    failure.
    """

    def __init__(
        self,
        port: Optional[int] = None,
        *,
        gateway_port: Optional[int] = None,
        registry_dir: Optional[str] = None,
        enable_gateway_failover: bool = True,
        strict_gateway: bool = False,
        enable_file_logging: bool = True,
        enable_job_persistence: bool = True,
        enable_telemetry: bool = True,
        enable_checkpoint_persistence: bool = True,
        enable_checkpoint_tools: bool = True,
        job_retention_hours: Optional[int] = None,
        checkpoint_path: Optional[str] = None,
        cli_binary: Optional[str] = None,
        control_file: Optional[str] = None,
        root: Optional[str] = None,
    ) -> None:
        self._host_dispatcher = QueueDispatcher()
        self._host_driver = StandaloneHost(self._host_dispatcher, thread_name="dcc-mcp-pdfcraft-host")
        execution_bridge = HostExecutionBridge(
            dispatcher=self._host_dispatcher,
            host_dispatcher=self._host_dispatcher,
            default_thread_affinity="any",
            default_execution="sync",
            default_timeout_hint_secs=60,
        )
        options = DccServerOptions.from_env(
            "pdfcraft",
            Path(__file__).resolve().parent / "skills",
            port=DEFAULT_PORT if port is None else port,
            server_name="dcc-mcp-pdfcraft",
            server_version=__version__,
            execution_bridge=execution_bridge,
            gateway_port=gateway_port,
            registry_dir=registry_dir,
            enable_gateway_failover=enable_gateway_failover,
            strict_gateway=strict_gateway,
            enable_file_logging=enable_file_logging,
            enable_job_persistence=enable_job_persistence,
            enable_telemetry=enable_telemetry,
            enable_checkpoint_persistence=enable_checkpoint_persistence,
            enable_checkpoint_tools=enable_checkpoint_tools,
            job_retention_hours=job_retention_hours,
            checkpoint_path=checkpoint_path,
        )
        super().__init__(options)

        self._cli_binary = cli_binary or os.environ.get(ENV_CLI)
        self._control_file = control_file or os.environ.get(ENV_CONTROL_FILE)
        self._root = root or os.environ.get(ENV_ROOT)
        self._readiness = AdapterReadinessBinder(self, dcc_ready_probe=self._host_ready)
        register_actions(self)

    # -- host surfaces -----------------------------------------------------

    def _host_ready(self) -> bool:
        """Report whether any PdfCraft entry point is usable right now."""
        if self._cli_binary:
            return Path(self._cli_binary).is_file()
        return find_binary("pdfcraft-cli") is not None

    def control_channel(self) -> ControlChannel:
        """Build a control channel to the running PdfCraft instance.

        The endpoint is read on every call because the app picks a random port
        each time it starts with ``--control``; a cached channel would point at
        a dead port after a restart. This costs nothing: ``ControlChannel.call``
        already opens a fresh connection and authenticates per request, so the
        only thing rebuilt here is a port/token pair.
        """
        if not self._control_file:
            raise ControlChannelUnavailable(
                f"no control file configured; start PdfCraft with --control <file> or set {ENV_CONTROL_FILE}"
            )
        return ControlChannel(read_endpoint(self._control_file))

    @property
    def root(self) -> Optional[str]:
        return self._root

    @property
    def cli_binary(self) -> Optional[str]:
        return self._cli_binary

    def host_status(self) -> dict[str, Any]:
        """Describe the detected host binaries for diagnostics."""
        binaries = discover()
        return {
            "cli": str(binaries.cli) if binaries.cli else None,
            "app": str(binaries.app) if binaries.app else None,
            "configured_cli": self._cli_binary,
            "control_file": self._control_file,
            "root": self._root,
        }

    # -- lifecycle ---------------------------------------------------------

    def start(self, **kwargs: Any) -> Any:
        self._host_driver.start()
        self._readiness.bind_queue_dispatcher(self._host_dispatcher)
        self._readiness.mark_execution_ready()
        self._readiness.mark_dispatcher_ready()
        self._readiness.refresh_dcc_ready()
        self._readiness.publish()
        return super().start(**kwargs)

    def stop(self, **kwargs: Any) -> Any:
        try:
            return super().stop(**kwargs)
        finally:
            self._host_driver.stop()


_server: Optional[PdfCraftMcpServer] = None


def start_server(**kwargs: Any) -> PdfCraftMcpServer:
    """Start the process-wide PdfCraft MCP server."""
    global _server
    if _server is None:
        _server = PdfCraftMcpServer(**kwargs)
        _server.start()
    return _server


def stop_server() -> None:
    """Stop the process-wide server, if one is running."""
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def main() -> None:
    """Run the standalone adapter until interrupted."""
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    start_server()
    try:
        stopped.wait()
    finally:
        stop_server()
