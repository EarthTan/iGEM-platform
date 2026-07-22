"""setup lib package."""
from .manifest import (
    ManifestError, load, save, list_sources, get_source,
    filter_sources, resolve_path, set_sha256, promote_to_latest,
    PUBLIC_DATA_ROOT, DEFAULT_MANIFEST_PATH as MANIFEST_PATH,
)
from .legacy_sources import LEGACY_SOURCES, LEGACY_ROOT

__all__ = [
    "ManifestError", "load", "save", "list_sources", "get_source",
    "filter_sources", "resolve_path", "set_sha256", "promote_to_latest",
    "PUBLIC_DATA_ROOT", "MANIFEST_PATH",
    "LEGACY_SOURCES", "LEGACY_ROOT",
]