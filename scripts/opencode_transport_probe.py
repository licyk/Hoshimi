"""Compare one anonymous request through Python TLS and OpenCode's embedded Bun.

Run in the same Python environment and terminal used to start Hoshimi:
    python scripts/opencode_transport_probe.py

Reads Hoshimi's configured OpenCode network policy. Does not change configuration,
use configured credentials, or touch a running server's cooldowns. Sends exactly
one request per selected transport, with identical body bytes, headers and proxy
selection. Use --python-only --headers service to preserve service headers without
requiring a Bun runtime; --prompt can match the original API request.
Proxy credentials and request contents are sent to Bun over stdin, never argv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import OpenSSL
from OpenSSL import SSL

from hoshimi.core.config import load_settings
from hoshimi.core.net.opencode_tls import OpenCodeTransport
from hoshimi.core.net.policy import parse_policy, proxy_route
from hoshimi.core.providers.base import Account
from hoshimi.core.providers.opencode import OpencodeProvider
from hoshimi.version import VERSION

_BUN_FETCH = r"""
const input = JSON.parse(await Bun.stdin.text());
try {
  const response = await fetch(input.url, {
    method: 'POST', headers: input.headers, body: input.body,
    proxy: input.proxy || '', redirect: 'manual',
    signal: AbortSignal.timeout(input.timeout * 1000),
    tls: process.env.SSL_CERT_FILE ? {ca: await Bun.file(process.env.SSL_CERT_FILE).text()} : undefined,
  });
  console.log(JSON.stringify({status: response.status, body: await response.text(),
    retry_after: response.headers.get('retry-after'), bun: Bun.version}));
} catch (error) {
  console.log(JSON.stringify({error: error.name, bun: Bun.version}));
}
"""


def capture_request(model: str, prompt: str = "Reply with only OK. Do not call tools.") -> httpx.Request:
    requests = []

    def capture(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: [DONE]\n\n")

    with httpx.Client(transport=httpx.MockTransport(capture)) as client:
        account = Account({"id": "opencode-public"}, provider_id="opencode", anonymous=True)
        provider = OpencodeProvider(client, [account])
        list(provider.stream_raw(account, model, {
            "messages": [{"role": "user", "content": prompt}], "max_tokens": 256,
        }))
    assert len(requests) == 1
    return requests[0]


def summarize(body: str) -> dict:
    """Expose only model text and structured errors, not raw headers or HTML."""
    try:
        event = json.loads(body)
    except ValueError:
        event = None
    if isinstance(event, dict):
        error = event.get("error", {})
        if isinstance(error, dict):
            return {"error_type": error.get("type") or error.get("code"), "message": str(error.get("message", ""))[:400]}
        return {"error": str(error)[:400]}
    text, errors, done = [], [], False
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        if line[6:] == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if "error" in event:
            errors.append(summarize(json.dumps(event)))
        for choice in event.get("choices", []):
            text.append(choice.get("delta", {}).get("content") or "")
    return {"answer": "".join(text)[:400], "done": done, "errors": errors}


def native_executable(value: str, *, windows: bool) -> str:
    """Resolve Git Bash drive paths and reject launchers CreateProcess cannot run."""
    if windows and (match := re.match(r"^/([a-zA-Z])/(.*)$", value)):
        value = f"{match[1].upper()}:/{match[2]}"
    path = Path(value).expanduser()
    if windows and path.suffix.lower() != ".exe" and path.with_suffix(".exe").is_file():
        path = path.with_suffix(".exe")
    if not path.is_file():
        raise ValueError("OpenCode executable not found; pass --opencode with its full native path, or use --python-only")
    if windows:
        with path.open("rb") as binary:
            if binary.read(2) != b"MZ":
                raise ValueError("This is a launcher, not a Windows executable; pass the actual opencode.exe, or use --python-only")
    return str(path.resolve())


def error_details(exc: Exception) -> dict:
    result = {"error": type(exc).__name__}
    if isinstance(exc, OSError):
        result.update(errno=exc.errno, winerror=getattr(exc, "winerror", None))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", help="Path to the OpenCode executable (not an npm .cmd shim)")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--python-only", action="store_true", help="Only test Python; no OpenCode executable required")
    parser.add_argument("--headers", choices=("controlled", "service"), default="controlled", help="Keep service defaults or standardize compression/connection headers")
    parser.add_argument("--prompt", default="Reply with only OK. Do not call tools.")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    executable = None
    if not args.python_only:
        candidate = args.opencode or shutil.which("opencode") or str(Path.home() / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode"))
        try:
            executable = native_executable(candidate, windows=os.name == "nt")
        except (OSError, ValueError) as exc:
            parser.error(str(exc))

    settings = load_settings()
    cfg = settings.providers.get("opencode")
    policy = parse_policy(cfg.options.get("network") if cfg else None, inherited=settings.network)
    request = capture_request("deepseek-v4.1-flash-free", args.prompt)
    proxy, route = proxy_route(request.url, policy)
    if not args.python_only and proxy and httpx.URL(proxy).scheme not in ("http", "https"):
        parser.error("This Bun comparison requires an HTTP(S) proxy; no direct fallback will be attempted")
    headers = dict(request.headers)
    if args.headers == "controlled":
        headers = {key: value for key, value in headers.items() if key not in ("host", "content-length")}
        headers["accept-encoding"] = "identity"
        headers["connection"] = "close"
    record = {"url": str(request.url), "headers": headers, "body": request.content.decode(), "proxy": proxy, "timeout": args.timeout}
    print(json.dumps({
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hoshimi": VERSION, "python": platform.python_version(), "platform": platform.system(),
        "pyopenssl": OpenSSL.__version__, "openssl": SSL.SSLeay_version(SSL.SSLEAY_VERSION).decode(),
        "route": route, "proxy_scheme": httpx.URL(proxy).scheme if proxy else None,
        "header_profile": args.headers, "accept_encoding": headers.get("accept-encoding"), "connection": headers.get("connection"),
        "account": "anonymous", "body_sha256": hashlib.sha256(request.content).hexdigest(),
    }), flush=True)

    start = time.monotonic()
    try:
        with httpx.Client(transport=OpenCodeTransport(proxy=proxy), trust_env=False, timeout=args.timeout) as client:
            response = client.post(request.url, headers=headers, content=request.content)
            result = {"status": response.status_code, "retry_after": response.headers.get("retry-after"), **summarize(response.text)}
    except (httpx.HTTPError, SSL.Error, OSError, ValueError) as exc:
        result = error_details(exc)
    print(json.dumps({"transport": "pyopenssl", "seconds": round(time.monotonic() - start, 2), **result}, ensure_ascii=False), flush=True)
    if args.python_only:
        return

    # The proxy was resolved above for both clients. Prevent Bun from choosing a
    # different environment route or bypass list. Retain certificate settings.
    env = {key: value for key, value in os.environ.items() if key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env["BUN_BE_BUN"] = "1"
    start = time.monotonic()
    assert executable is not None
    try:
        completed = subprocess.run([executable, "--eval", _BUN_FETCH], input=json.dumps(record), text=True, encoding="utf-8", capture_output=True, env=env, timeout=args.timeout + 5, check=True)
        raw = json.loads(completed.stdout)
        result = {key: raw[key] for key in ("status", "retry_after", "bun", "error") if key in raw}
        if "body" in raw:
            result.update(summarize(raw["body"]))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        result = error_details(exc)
    print(json.dumps({"transport": "opencode-bun", "seconds": round(time.monotonic() - start, 2), **result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
