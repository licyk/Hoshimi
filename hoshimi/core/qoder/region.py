"""The two Qoder regions and every URL the CLI protocol needs.

Global (``qoder.com`` / ``api3.qoder.sh``) and China (``qoder.com.cn`` /
``gateway.qoder.com.cn``) are separate services: separate accounts, separate
catalogues, separate Personal Access Tokens. A PAT minted on one is rejected by
the other, which is why the region is a property of the lane and never guessed
from the token.

Chat traffic is not bound to the catalog node: ``region/endpoints`` publishes a
per-account node map and the official CLI drives agent chat against
``dataNodeMap.remote_agent``. The override lives here so the catalog keeps using
the inference node while chat follows the resolved one.

Ported from ``pi-provider-qoder`` ``src/region.ts``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class QoderRegion:
    """One region's hosts and the identity fallbacks its COSY payload needs."""

    mode: str
    provider_id: str
    base_url: str
    open_api_url: str
    center_url: str
    manage_url: str
    pat_manage_url: str
    model_cache_file: str
    pat_env_names: tuple[str, ...]
    login_name: str
    user_name_fallback: str
    user_email_fallback: str
    supports_browser_login: bool = False
    device_login_url: str = ""


GLOBAL = QoderRegion(
    mode="global",
    provider_id="qodercli",
    base_url="https://api3.qoder.sh/",
    open_api_url="https://openapi.qoder.sh",
    center_url="https://center.qoder.sh",
    manage_url="https://qoder.com",
    pat_manage_url="https://qoder.com/account/integrations",
    model_cache_file="qodercli-models.json",
    pat_env_names=("QODER_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN", "QODER_PAT"),
    login_name="Qoder CLI (Personal Access Token)",
    user_name_fallback="Qoder User",
    user_email_fallback="user@qoder.com",
    supports_browser_login=True,
    device_login_url="https://qoder.com/device/selectAccounts",
)

CN = QoderRegion(
    mode="cn",
    provider_id="qodercli-cn",
    base_url="https://gateway.qoder.com.cn/",
    open_api_url="https://openapi.qoder.com.cn",
    center_url="https://gateway.qoder.com.cn",
    manage_url="https://qoder.com.cn",
    pat_manage_url="https://qoder.com.cn/account/integrations",
    model_cache_file="qodercli-cn-models.json",
    pat_env_names=("QODERCN_API_KEY", "QODERCN_PERSONAL_ACCESS_TOKEN", "QODERCN_PAT"),
    login_name="Qoder CLI CN (Personal Access Token)",
    user_name_fallback="Qoder CN User",
    user_email_fallback="user@qoder.com.cn",
)

REGIONS: dict[str, QoderRegion] = {region.mode: region for region in (GLOBAL, CN)}
BY_PROVIDER: dict[str, QoderRegion] = {region.provider_id: region for region in (GLOBAL, CN)}


def region_for(provider_id: str) -> QoderRegion:
    """The region a lane id belongs to; unknown ids fall back to global."""
    return BY_PROVIDER.get(provider_id, GLOBAL)


def normalize_base_url(url: str) -> str:
    """A node entry as a base URL: scheme added, exactly one trailing slash."""
    trimmed = str(url or "").strip()
    if not trimmed:
        return ""
    if "://" not in trimmed:
        trimmed = f"https://{trimmed}"
    return trimmed if trimmed.endswith("/") else f"{trimmed}/"


def model_list_url(region: QoderRegion) -> str:
    return f"{region.base_url}algo/api/v2/model/list?Encode=1"


def chat_url(region: QoderRegion, chat_base_url: str) -> str:
    """The agent-chat endpoint on a resolved node (not necessarily the catalog node)."""
    base = normalize_base_url(chat_base_url) or region.base_url
    return f"{base}algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"


def exchange_url(region: QoderRegion) -> str:
    return f"{region.open_api_url}/api/v1/jobToken/exchange"


def user_info_url(region: QoderRegion) -> str:
    return f"{region.open_api_url}/api/v1/userinfo"


def usage_url(region: QoderRegion) -> str:
    return f"{region.open_api_url}/api/v2/quota/usage"


def refresh_url(region: QoderRegion) -> str:
    return f"{region.center_url}/algo/api/v3/user/refresh_token"


def region_endpoints_url(region: QoderRegion) -> str:
    return f"{region.center_url}/algo/api/v3/service/region/endpoints"


def device_login_url(region: QoderRegion, challenge: str, machine_id: str, nonce: str) -> str:
    if not region.device_login_url:
        raise ValueError(f"{region.provider_id}: browser login is not available in this region")
    return f"{region.device_login_url}?challenge={challenge}&challenge_method=S256&machine_id={machine_id}&nonce={nonce}"


def device_poll_url(region: QoderRegion, nonce: str, verifier: str) -> str:
    from urllib.parse import quote

    return f"{region.open_api_url}/api/v1/deviceToken/poll?nonce={quote(nonce)}&verifier={quote(verifier)}&challenge_method=S256"


class ChatEndpointOverride:
    """The node chat currently runs on, per region.

    Kept apart from the region table because it is mutable account state: the
    resolver writes it after discovery and after a daily-limit rotation, and a
    later request signs against whatever it holds.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: dict[str, str] = {}

    def get(self, region: QoderRegion) -> str:
        with self._lock:
            return self._overrides.get(region.mode) or region.base_url

    def set(self, region: QoderRegion, url: str | None) -> None:
        normalized = normalize_base_url(url or "")
        with self._lock:
            if not normalized:
                self._overrides.pop(region.mode, None)
            else:
                self._overrides[region.mode] = normalized

    def clear(self) -> None:
        with self._lock:
            self._overrides.clear()