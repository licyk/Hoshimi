"""``web_search`` and ``web_fetch``: looking things up, and reading a page.

Search asks DuckDuckGo's light page (no key), or a SearXNG instance named in
Settings (``playground.search_url``). A fetched page comes back as Markdown,
converted here with the standard library: headings, paragraphs, links, lists,
code and tables survive; scripts, styles and navigation do not, and a page's
``<main>`` or ``<article>`` is preferred over the rest of it.

A fetch only goes to public addresses: a name that resolves to a loopback,
private, link-local or otherwise reserved address is refused, at every
redirect. The server holds account credentials and answers on loopback; a
model must not be able to read it, or anything else on the local network.
Both tools follow the gateway's network settings (its proxy).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from hoshimi.core.agent.base import Tool, ToolContext, ToolError, ToolResult, clip, integer, string
from hoshimi.core.net.policy import proxy_route

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 60.0
"""Seconds a request may wait for a reply, or between two parts of one: long enough for a slow connection."""
CONNECT_TIMEOUT = 30.0
"""Seconds a request may take to connect."""
MAX_BYTES = 5_000_000
"""The most of a page that is downloaded."""
FETCH_CHARS = 60_000
"""The most of a page a model is sent unless it asks for less."""
MAX_REDIRECTS = 5
SEARCH_RESULTS = 8
DUCKDUCKGO = "https://lite.duckduckgo.com/lite/"


def _client(ctx: ToolContext, url: str) -> httpx.Client:
    proxy, _ = proxy_route(httpx.URL(url), ctx.network)
    if ctx.transport is not None:
        return httpx.Client(transport=ctx.transport, timeout=TIMEOUT, follow_redirects=False)
    return httpx.Client(proxy=proxy, timeout=httpx.Timeout(TIMEOUT, connect=CONNECT_TIMEOUT), follow_redirects=False, trust_env=False)


# -- addresses -------------------------------------------------------------------- #
def public_address(host: str) -> bool:
    """Whether every address ``host`` resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return True  # It does not resolve here; the request fails on its own (or a proxy resolves it).
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            return False
    return True


def check_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("Only http:// and https:// addresses can be fetched.")
    if parsed.username or parsed.password:
        raise ToolError("Addresses with a user name or password cannot be fetched.")
    host = parsed.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or not public_address(host):
        raise ToolError(f"{host} is a local or private address; web_fetch only reads public pages.")
    return url


# -- fetching ----------------------------------------------------------------------- #
def fetch(ctx: ToolContext, url: str) -> tuple[str, str, str]:
    """``(final address, content type, text)`` of ``url``, following redirects to public addresses only."""
    current = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        ctx.check()
        with _client(ctx, current) as client, client.stream("GET", current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"}) as response:
            if response.is_redirect and "location" in response.headers:
                current = check_url(urljoin(current, response.headers["location"]))
                continue
            if response.status_code >= 400:
                raise ToolError(f"{current} answered {response.status_code} {response.reason_phrase}.")
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if kind and not (kind.startswith("text/") or kind in ("application/json", "application/xml", "application/xhtml+xml", "application/rss+xml", "application/atom+xml", "application/javascript")):
                raise ToolError(f"{current} is {kind}, not a page or text; only text can be read.")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) >= MAX_BYTES:
                    break
                ctx.check()
            encoding = response.charset_encoding or _meta_charset(bytes(body[:4096])) or "utf-8"
            try:
                text = bytes(body[:MAX_BYTES]).decode(encoding, errors="replace")
            except LookupError:
                text = bytes(body[:MAX_BYTES]).decode("utf-8", errors="replace")
            return str(response.url), kind, text
    raise ToolError(f"{url} redirected more than {MAX_REDIRECTS} times.")


def _meta_charset(head: bytes) -> str | None:
    match = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", head, re.IGNORECASE)
    return match.group(1).decode("ascii") if match else None


def _web_fetch(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = string(args, "url").strip()
    if "://" not in url:
        url = "https://" + url
    limit = integer(args, "max_length", FETCH_CHARS, low=1000, high=FETCH_CHARS)
    try:
        final, kind, text = fetch(ctx, url)
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not fetch {url}: {exc.__class__.__name__}{': ' + str(exc) if str(exc) else ''}") from None
    title = ""
    if kind in ("text/html", "application/xhtml+xml") or (not kind and "<html" in text[:2000].lower()):
        title, text = html_to_markdown(text, final)
    head = f"URL: {final}\n" + (f"Title: {title}\n" if title else "") + "\n"
    cut = len(text) > limit
    body = text[:limit] + (f"\n\n[Cut at {limit} of {len(text)} characters.]" if cut else "")
    return ToolResult(head + body, meta={"url": final, "title": title})


# -- HTML as Markdown -------------------------------------------------------------- #
_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "head", "nav", "footer", "form", "button", "select", "canvas", "dialog"}
_BLOCK = {"p", "div", "section", "article", "main", "header", "aside", "figure", "figcaption", "blockquote", "details", "summary", "dl", "dt", "dd", "address", "table", "ul", "ol", "hr"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr", "param"}


class _Markdown(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.main: list[str] | None = None
        self.main_depth = 0
        self.skip = 0
        self.pre = 0
        self.lists: list[list[int]] = []
        self.link: str | None = None
        self.link_text: list[str] = []
        self.title = ""
        self.in_title = False
        self.cells = 0
        self.rows = 0

    # Where text goes: the link being read, or the page (and its main part).
    def emit(self, text: str) -> None:
        if self.link is not None:
            self.link_text.append(text)
            return
        self.out.append(text)
        if self.main is not None and self.main_depth:
            self.main.append(text)

    def block(self, gap: str = "\n\n") -> None:
        self.emit(gap)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        if self.skip:
            if tag in _SKIP and tag not in _VOID:
                self.skip += 1
            return
        if tag in _SKIP:
            self.skip = 1
            return
        if tag in ("main", "article"):
            self.main = self.main if self.main is not None else []
            self.main_depth += 1
        attr = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.block()
            self.emit("#" * int(tag[1]) + " ")
        elif tag == "pre":
            self.block()
            self.emit("```\n")
            self.pre += 1
        elif tag == "code" and not self.pre:
            self.emit("`")
        elif tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")
        elif tag == "br":
            self.emit("\n")
        elif tag in ("ul", "ol"):
            self.block("\n")
            self.lists.append([0 if tag == "ol" else -1])
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            marker = "-"
            if self.lists and self.lists[-1][0] >= 0:
                self.lists[-1][0] += 1
                marker = f"{self.lists[-1][0]}."
            self.emit("\n" + "  " * depth + marker + " ")
        elif tag == "a" and attr.get("href") and not str(attr.get("href")).startswith(("javascript:", "#")):
            self.link, self.link_text = urljoin(self.base, str(attr["href"])), []
        elif tag == "img" and attr.get("alt"):
            self.emit(f"[image: {attr['alt']}]")
        elif tag == "tr":
            self.emit("\n|")
            self.cells = 0
        elif tag in ("td", "th"):
            self.emit(" ")
        elif tag in _BLOCK:
            if tag == "table":
                self.rows = 0
            self.block()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if self.skip:
            if tag in _SKIP:
                self.skip -= 1
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self.block()
        elif tag == "pre":
            self.pre = max(self.pre - 1, 0)
            self.emit("\n```")
            self.block()
        elif tag == "code" and not self.pre:
            self.emit("`")
        elif tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.block("\n")
        elif tag == "a" and self.link is not None:
            text = re.sub(r"\s+", " ", "".join(self.link_text)).strip()
            link, self.link = self.link, None
            self.emit(f"[{text}]({link})" if text else "")
        elif tag in ("td", "th"):
            self.emit(" |")
            self.cells += 1
        elif tag == "tr":
            if self.rows == 0 and self.cells:
                self.emit("\n|" + " --- |" * self.cells)
            self.rows += 1
        elif tag in _BLOCK:
            self.block()
        if tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        if self.skip:
            return
        if self.pre:
            self.emit(data)
        else:
            self.emit(re.sub(r"\s+", " ", data))


def html_to_markdown(html: str, base: str = "") -> tuple[str, str]:
    """``(title, markdown)`` of a page."""
    parser = _Markdown(base)
    parser.feed(html)
    parser.close()
    main = "".join(parser.main or [])
    text = main if len(_tidy(main)) >= 200 else "".join(parser.out)
    return re.sub(r"\s+", " ", unescape(parser.title)).strip(), _tidy(text)


def _tidy(text: str) -> str:
    """Whitespace folded outside code blocks (list items keep their indent), and no more than one blank line."""
    out: list[str] = []
    fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if fence and out and not out[-1]:
                out.pop()
            fence = not fence
            out.append(line.strip())
        elif fence:
            out.append(line.rstrip())
        else:
            item = re.match(r"( *)(?:-|\d+\.) ", line)
            out.append((item.group(1) if item else "") + re.sub(r"\s+", " ", line).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# -- searching ---------------------------------------------------------------------- #
class _DuckDuckGo(HTMLParser):
    """Results of DuckDuckGo's light page: a ``result-link`` anchor, then a ``result-snippet`` cell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = str(attr.get("class") or "").split()
        if tag == "a" and "result-link" in classes:
            href = str(attr.get("href") or "")
            target = parse_qs(urlparse(href).query).get("uddg", [""])[0] if "duckduckgo.com/l/" in href else href
            if target.startswith("//"):
                target = "https:" + target
            self.results.append({"title": "", "url": target, "snippet": ""})
            self.field = "title"
        elif tag == "td" and "result-snippet" in classes and self.results:
            self.field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if (tag == "a" and self.field == "title") or (tag == "td" and self.field == "snippet"):
            self.field = None

    def handle_data(self, data: str) -> None:
        if self.field and self.results:
            self.results[-1][self.field] += data


def search(ctx: ToolContext, query: str, count: int) -> list[dict[str, str]]:
    if ctx.search_url:
        url = ctx.search_url.rstrip("/") + "/search"
        with _client(ctx, url) as client:
            response = client.get(url, params={"q": query, "format": "json"}, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        if response.status_code != 200:
            raise ToolError(f"The search service answered {response.status_code}; check playground.search_url (SearXNG needs its JSON format turned on).")
        try:
            rows = response.json().get("results") or []
        except ValueError:
            raise ToolError("The search service did not answer with JSON; SearXNG needs its JSON format turned on.") from None
        results = [{"title": str(r.get("title") or ""), "url": str(r.get("url") or ""), "snippet": str(r.get("content") or "")} for r in rows if isinstance(r, dict)]
    else:
        with _client(ctx, DUCKDUCKGO) as client:
            response = client.get(DUCKDUCKGO, params={"q": query}, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        if response.status_code != 200:
            raise ToolError("DuckDuckGo did not answer the search (it limits how often it is asked); try again shortly, or set a SearXNG address in Settings.")
        parser = _DuckDuckGo()
        parser.feed(response.text)
        results = [r for r in parser.results if r["url"].startswith("http") and "duckduckgo.com/y.js" not in r["url"]]
    tidy = [{k: re.sub(r"\s+", " ", v).strip() for k, v in r.items()} for r in results]
    return [r for r in tidy if r["url"]][:count]


def _web_search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = string(args, "query").strip()
    count = integer(args, "max_results", SEARCH_RESULTS, low=1, high=20)
    try:
        results = search(ctx, query, count)
    except httpx.HTTPError as exc:
        raise ToolError(f"The search failed: {exc.__class__.__name__}{': ' + str(exc) if str(exc) else ''}") from None
    if not results:
        return ToolResult(f"No results for “{query}”.", meta={"query": query, "results": 0})
    lines = [f"Results for “{query}”:"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title'] or r['url']}\n   {r['url']}" + (f"\n   {r['snippet']}" if r["snippet"] else ""))
    return ToolResult(clip("\n".join(lines)), meta={"query": query, "results": len(results)})


WEB_SEARCH = Tool(
    name="web_search",
    group="web_search",
    description="Search the web. Returns the top results with their titles, addresses and a snippet each; read a result in full with web_fetch.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "max_results": {"type": "integer", "description": f"How many results. Default {SEARCH_RESULTS}, at most 20."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    run=_web_search,
)

WEB_FETCH = Tool(
    name="web_fetch",
    group="web_fetch",
    description=(
        "Read a web page (or any text) at a public http(s) address. A page comes back as Markdown, its main content "
        "preferred; long ones are cut. Local and private network addresses cannot be read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The address to read."},
            "max_length": {"type": "integer", "description": f"The most characters to return. Default and most {FETCH_CHARS}."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    run=_web_fetch,
)
