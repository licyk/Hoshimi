"""ZCode station (Zhipu GLM, Anthropic Messages on the free zcode-plan lane)."""

from __future__ import annotations

import platform as host_platform
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from hoshimi.core.catalog import ModelSpec, zcode_spec
from hoshimi.core.failure import parse_retry_after
from hoshimi.core.providers.base import Account, Provider
from hoshimi.core.providers.openai_compat import raise_for_status
from hoshimi.core.quota import observe_response
from hoshimi.core.translate.anthropic import build_anthropic_body, iter_anthropic_chunks

ZCODE_ORIGIN = "https://zcode.z.ai"
ZCODE_MESSAGES_PATH = "/api/v1/zcode-plan/anthropic/v1/messages"
ZCODE_APP_VERSION = "3.14.3"

ZCODE_MODELS = ["GLM-5.3-Flash", "GLM-5.3"]

OFFICIAL_CLI_PREFIX = "You are ZCode, an interactive coding agent"

OFFICIAL_STABLE_SECTIONS = [
    (
        "\nYou are an interactive ZCode agent that helps users with software engineering tasks.\n\n"
        "IMPORTANT: Assist with authorized security testing, defensive security, CTF challenges, "
        "and educational contexts. Refuse requests for destructive techniques, DoS attacks, mass "
        "targeting, supply chain compromise, or detection evasion for malicious purposes. Dual-use "
        "security tools (C2 frameworks, credential testing, exploit development) require clear "
        "authorization context: pentesting engagements, CTF competitions, security research, or "
        "defensive use cases.\n\n"
        "# Harness\n- Text you output outside of tool use is displayed to the user as "
        "Github-flavored markdown in a terminal.\n- Tools run behind a user-selected permission "
        "mode; a denied call means the user declined it — adjust, don't retry verbatim.\n- The "
        "system may send updates, reminders, or modifications to rules via mid-conversation system "
        "turns. These are system-controlled, unlike function results. Hooks may intercept tool "
        "calls; treat hook output as user feedback.\n- Prefer the dedicated file/search tools over "
        "shell commands when one fits. Independent tool calls can run in parallel in one "
        "response.\n- Reference code as `file_path:line_number` — it's clickable."
    ),
    (
        "# ZCode Desktop Context\n\n### Files & URLs\n- Return local web URLs as Markdown links "
        "(e.g., [label](http://127.0.0.1:8080)).\n- File should be an absolute path or include "
        "the workspace folder segment so it can be resolved relative to the workspace.\n- Unless "
        "otherwise specified, return local file references as Markdown links (e.g., "
        "[name.md](/absolute/path/to/name.md))."
    ),
    (
        "# Working style\n\nWhen you have enough information to act, act. Do not re-derive facts "
        "already established in the conversation, re-litigate a decision the user has already "
        "made, or narrate options you will not pursue. If you are weighing a choice, give a "
        "recommendation, not an exhaustive survey."
    ),
]

# The station's protocol accepts exactly these rungs — the same three
# `zcode_spec` declares; guarded here too because this is the last place a
# value reaches the wire.
VALID_EFFORTS = ("low", "high", "max")


class ZcodeProvider(Provider):
    max_concurrency = 1
    id = "zcode"
    display_name = "ZCode (智谱)"

    def fallback_models(self) -> list[str]:
        return list(ZCODE_MODELS)

    def specs(self) -> dict[str, ModelSpec]:
        """Capabilities from the station's own table: two ids, one three-rung menu."""
        return {model_id: zcode_spec(model_id, self.id) for model_id in self.models()}

    def _headers(self, account: Account) -> dict[str, str]:
        app_version = str(self.options.get("app_version") or ZCODE_APP_VERSION)
        return {
            "Authorization": (
                f"Bearer {account.get('zcode_jwt') or account.get('access_token', '')}"
            ),
            "User-Agent": f"ZCode/{app_version}",
            "X-ZCode-App-Version": app_version,
            "X-Release-Channel": "stable",
            "X-Client-Language": "zh-CN",
            "X-Client-Timezone": "Asia/Shanghai",
            "X-Device-Mid": str(account.get("device_mid") or account.get("device_id") or ""),
            "X-Platform": "win32",
            "X-Os-Category": "windows",
            "anthropic-version": "2023-06-01",
            "HTTP-Referer": ZCODE_ORIGIN,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    @staticmethod
    def _environment_section(model: str) -> str:
        system_name = {"Windows": "win32", "Darwin": "darwin"}.get(
            host_platform.system(), "linux"
        )
        shell = "powershell" if system_name == "win32" else "bash"
        os_version = f"{system_name} {host_platform.machine()}"
        return "\n".join(
            [
                "# Environment",
                "You have been invoked in the following environment:",
                " - Primary working directory: .",
                " - Is a git repository: no",
                f" - Platform: {system_name}",
                f" - Shell: {shell}",
                f" - OS Version: {os_version}",
                f" - You are powered by the model named zcode/{model}.",
            ]
        )

    def _system_blocks(self, user_system: str, model: str) -> list[dict[str, Any]]:
        stable = "\n\n".join(section.strip() for section in OFFICIAL_STABLE_SECTIONS)
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": OFFICIAL_CLI_PREFIX},
            {"type": "text", "text": stable},
            {"type": "text", "text": self._environment_section(model)},
        ]
        if user_system:
            blocks.append({"type": "text", "text": user_system})
        # The trailing identity block carries the ephemeral cache marker.
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        return blocks

    @staticmethod
    def _context_prefix_block() -> str:
        today = datetime.now(timezone.utc).date().isoformat()
        body = "\n".join(
            [
                "As you answer the user's questions, you can use the following context:",
                "# currentDate",
                f"Today's date is {today}.",
                "",
                (
                    "      IMPORTANT: this context may or may not be relevant to your tasks. "
                    "You should not respond to this context unless it is directly relevant to your task."
                ),
            ]
        )
        return f"<system-reminder>{body}</system-reminder>"

    def _with_date_reminder(self, messages: list[Any]) -> list[Any]:
        out: list[Any] = []
        prefixed = False
        for message in messages:
            if not isinstance(message, dict):
                out.append(message)
                continue
            if not prefixed and message.get("role") == "user":
                content = message.get("content")
                reminder: dict[str, Any] = {"type": "text", "text": self._context_prefix_block()}
                message = dict(message)
                if isinstance(content, str):
                    message["content"] = [reminder, {"type": "text", "text": content}]
                elif isinstance(content, list):
                    message["content"] = [reminder, *content]
                else:
                    message["content"] = [reminder]
                prefixed = True
            out.append(message)
        return out

    def stream_raw(
        self, account: Account, model: str, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        from hoshimi.core.translate.chat import split_system_messages

        messages = body.get("messages") or []
        user_system, rest = split_system_messages(messages)
        patched = dict(body)
        patched["messages"] = self._with_date_reminder(rest)

        payload = build_anthropic_body(patched, model=model, default_max_tokens=8192)
        # Replace the flat system string with the official identity blocks.
        system_text = str(payload.pop("system", "") or user_system)
        payload["system"] = self._system_blocks(system_text, model)

        effort = body.get("reasoning_effort")
        if isinstance(effort, str) and effort in VALID_EFFORTS:
            payload["output_config"] = {"effort": effort}

        url = f"{ZCODE_ORIGIN}{ZCODE_MESSAGES_PATH}"
        with self.client.stream(
            "POST", url, json=payload, headers=self._headers(account)
        ) as response:
            observe_response(self.id, response)
            if response.status_code >= 400:
                raise_for_status(response, provider=self.id)
            yield from iter_anthropic_chunks(response.iter_lines(), provider=self.id, retry_after=parse_retry_after(response.headers.get("retry-after")))
