"""Operation-specific request metadata, separate from model payload profiles."""

from __future__ import annotations

import httpx


def request_operation(request: httpx.Request, current: str, login: bool) -> str:
    """Label calls without retaining paths, queries, tokens or request bodies."""
    if current and current != "login":
        return current
    path = request.url.path.lower()
    if "refresh" in path:
        return "token_refresh"
    if login:
        if "authenticate" in path or "poll" in path:
            return "auth_poll"
        if path.endswith(("/token", "/register")):
            return "token_exchange"
        return "auth_start"
    if "models" in path:
        return "catalog"
    if "balance" in path or "credit" in path:
        return "balance"
    if "checkin" in path or "check-in" in path:
        return "checkin"
    return "request"


def cline_headers(*, profile: str = "sdk", accept: str = "application/json", task_id: str | None = None) -> dict[str, str]:
    """Keep the WorkOS SDK and extension identities internally consistent."""
    headers = {
        "Content-Type": "application/json", "Accept": accept,
        "HTTP-Referer": "https://cline.bot", "X-Title": "Cline",
        "X-IS-MULTIROOT": "false", "X-CLIENT-TYPE": "cline-sdk",
    }
    if profile == "extension":
        # Pinned to the inspected pi-free snapshot; change with a parity check.
        headers.update({
            "User-Agent": "Cline/4.1.10", "X-PLATFORM": "Visual Studio Code",
            "X-PLATFORM-VERSION": "1.109.3", "X-CLIENT-TYPE": "VSCode Extension",
            "X-CLIENT-VERSION": "4.1.10", "X-CORE-VERSION": "4.1.10",
        })
        if task_id:
            headers["X-Task-ID"] = task_id
    elif profile != "sdk":
        raise ValueError("cline client_profile must be sdk or extension")
    return headers
