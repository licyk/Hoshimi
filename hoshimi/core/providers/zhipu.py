"""Zhipu (BigModel): the permanently free GLM slice of open.bigmodel.cn.

A fixed-base account channel, not an aggregator: the station, the wire and
the free roster are all known, so the only thing an account carries is the
key. The seven permanently free ids (免费 in the platform's own pricing
table) are the lane's fallback roster; the live ``/models`` listing is the
source whenever it answers, because an account also sees whatever else its
balance covers.

The international station (api.z.ai) is the same wire under another host:
``options.base_url`` moves the whole lane there.

GLM's thinking is a ``thinking: {type}`` switch, not a token ladder, and the
free variants publish no menu — so a text row stays inert and the caller's
body reaches the station untouched, except for the two models that are
thinking models by construction (Z1, V-Thinking), where the free lane's
budget ladder is the only control that reaches the wire.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from hoshimi.core.catalog import ModelSpec, zhipu_spec
from hoshimi.core.providers.base import Account, ProviderError
from hoshimi.core.providers.openai_compat import OpenAiCompatProvider

ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
"""The station's OpenAI root; `{base_url}/chat/completions` is where a turn goes."""

ZHIPU_FREE_MODELS = (
    "glm-4.7-flash",
    "glm-4.5-flash",
    "glm-4-flash-250414",
    "glm-z1-flash",
    "glm-4.6v-flash",
    "glm-4.1v-thinking-flash",
    "glm-4v-flash",
)
"""The permanently free roster, when the listing cannot be reached."""


class ZhipuProvider(OpenAiCompatProvider):
    """The GLM free lane: one base, one bearer, seven free ids as the floor."""

    id = "zhipu"
    display_name = "Zhipu GLM (BigModel)"
    keyless = False
    # The listing is the account's own view of the station; there is nothing
    # a ping would learn that the listing has not already said.
    probeable = False
    # A key from bigmodel.cn is this install's own credential.
    requires_authorization = True
    base_url = ZHIPU_BASE_URL
    chat_path = "/chat/completions"
    models_path: str | None = "/models"
    fallback_model_list: ClassVar[list[str]] = list(ZHIPU_FREE_MODELS)

    def __init__(
        self,
        client: httpx.Client,
        accounts: list[Account],
        options: dict[str, Any] | None = None,
    ):
        super().__init__(client, accounts, options)
        # The international station is the same wire under another host.
        override = str(self.options.get("base_url") or "").strip().rstrip("/")
        if override:
            self.base_url = override

    def _token(self, account: Account) -> str:
        for key in ("api_key", "access_token"):
            value = str(account.get(key) or "").strip()
            if value:
                return value
        raise ProviderError(400, "zhipu: the account needs api_key (from bigmodel.cn console)")

    def build_headers(self, account: Account, model: str) -> dict[str, str]:
        headers = super().build_headers(account, model)
        headers["Authorization"] = f"Bearer {self._token(account)}"
        return headers

    def specs(self) -> dict[str, ModelSpec]:
        return {model_id: zhipu_spec(model_id, self.id) for model_id in self.models()}
