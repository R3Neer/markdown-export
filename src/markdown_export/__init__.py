"""Portable Markdown exports for local vaults and document collections."""

from .core import (
    Diagnostic,
    ExportError,
    ExportOptions,
    ExportResult,
    ProjectConfig,
    build_export,
    load_config,
    write_export,
)

__version__ = "0.1.1"

__all__ = [
    "Diagnostic",
    "ExportError",
    "ExportOptions",
    "ExportResult",
    "ProjectConfig",
    "build_export",
    "load_config",
    "write_export",
    "__version__",
]
