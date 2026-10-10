"""PdfCraft adapter for DCC-MCP.

PdfCraft has no embedded Python interpreter, so unlike the Maya, Blender or
Houdini adapters this package never runs inside the host. It drives PdfCraft
from an external process over two surfaces:

* ``pdfcraft-cli`` -- headless subprocess; the primary path, and the only one
  that works without a window.
* the desktop app's loopback control channel -- authenticated, random-port,
  used only for actions that need the live UI.

Both are optional at import time. Nothing here imports a host module, so skill
discovery and ``--help`` work on a machine where PdfCraft is not installed.
"""

from .__version__ import __version__
from .actions import ADAPTER_ACTIONS
from .server import PdfCraftMcpServer, start_server, stop_server

__all__ = [
    "ADAPTER_ACTIONS",
    "PdfCraftMcpServer",
    "__version__",
    "start_server",
    "stop_server",
]
