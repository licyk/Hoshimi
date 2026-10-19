"""Resolve the embedding API lazily so basic commands avoid importing FastAPI."""

from typing import Any


def __getattr__(name: str) -> Any:
    """Load an embedding API export only when the caller requests it."""
    if name in ("Services", "build_services"):
        from hoshimi.core import context

        return getattr(context, name)
    if name == "create_app":
        from hoshimi.api.app import create_app

        return create_app
    raise AttributeError(f"module 'hoshimi' has no attribute {name!r}")


def __dir__() -> list[str]:
    from hoshimi import __all__

    return sorted(__all__)
