"""Hoshimi: an OpenAI-compatible gateway over free model stations (Cline, TRAE, ZCode, ...).

To embed the API and the web UI in another application:

    from hoshimi.api.app import create_app
    from hoshimi.core.context import build_services

    app = create_app(build_services())
"""

from hoshimi._exports import __dir__ as __dir__
from hoshimi._exports import __getattr__ as __getattr__
from hoshimi.version import VERSION as VERSION

__all__ = ["VERSION", "Services", "build_services", "create_app"]
