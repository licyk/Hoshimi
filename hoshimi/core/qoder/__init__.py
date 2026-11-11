"""The Qoder CLI protocol, ported from the ``pi-provider-qoder`` extension.

This package speaks the agent-chat path the official Qoder CLI uses, which is
not the public OpenAI-compatible endpoint the ``qoder`` / ``qodercn`` lanes use:
COSY-signed requests, a custom Base64 body envelope, per-account node discovery,
and a Personal Access Token exchanged for a job token.

Modules, in dependency order:

- :mod:`~hoshimi.core.qoder.region` — the two regions and their URLs;
- :mod:`~hoshimi.core.qoder.encoding` — the ``Encode=1`` body codec;
- :mod:`~hoshimi.core.qoder.cosy` — request signing (the impersonation layer);
- :mod:`~hoshimi.core.qoder.cosy_version` — the client version the gateway gates on;
- :mod:`~hoshimi.core.qoder.auth` — PAT exchange and identity;
- :mod:`~hoshimi.core.qoder.endpoints` — node discovery and daily-limit rotation;
- :mod:`~hoshimi.core.qoder.catalog` — the model catalogue and its specs;
- :mod:`~hoshimi.core.qoder.protocol` — the request envelope;
- :mod:`~hoshimi.core.qoder.thinking` — thinking-tag routing and DSML cleanup;
- :mod:`~hoshimi.core.qoder.stream` — the response envelopes and business codes.
"""

from __future__ import annotations

from hoshimi.core.qoder.region import CN, GLOBAL, REGIONS, QoderRegion, region_for

__all__ = ["CN", "GLOBAL", "REGIONS", "QoderRegion", "region_for"]