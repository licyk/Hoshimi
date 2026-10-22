"""Look for sideways overflow in the web UI of a running server, in real browsers.

Every scrolling area scrolls in one direction (``.app-scroll`` down, ``.app-scroll-x``
sideways; see tokens.css), so a page, a list, a menu or a dialog that can scroll
sideways is a layout bug. This walks every page at several widths, in Chromium
(with its scrollbars shown, as on a desktop) and in Firefox (whose scrollbars
float), opens what opens (the Playground's chat list and editor, menus,
dialogs), and reports each area that is wider inside than it shows, with what
sticks out of it.

Optional: needs Playwright (``pip install playwright && playwright install
chromium firefox``). Start a server first (``hoshimi serve``, or
``python scripts/dev.py dev``), ideally with a conversation in the Playground, then:

    python scripts/check_layout.py --url http://127.0.0.1:10600

It exits with 1 when it finds an overflow, so it can gate a release.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

PAGES = ["/overview", "/models", "/channels", "/usage", "/playground", "/settings"]
WIDTHS = [320, 360, 600, 900, 1280, 1600]

# Wide on purpose: a code block, a table, a row of steps scroll sideways on their own.
FIND = r"""() => {
  const out = [];
  const scrollers = [document.documentElement, ...document.querySelectorAll('*')].filter((e) => {
    if (e === document.documentElement) return true;
    const x = getComputedStyle(e).overflowX;
    return (x === 'auto' || x === 'scroll') && !e.classList.contains('app-scroll-x');
  });
  for (const s of scrollers) {
    if (s.scrollWidth <= s.clientWidth + 0.5) continue;
    const left = s === document.documentElement ? 0 : s.getBoundingClientRect().left + s.clientLeft;
    const limit = left + s.clientWidth;
    const culprits = [];
    for (const e of s.querySelectorAll('*')) {
      let p = e.parentElement, contained = false;
      while (p && p !== s) { if (getComputedStyle(p).overflowX !== 'visible') { contained = true; break; } p = p.parentElement; }
      if (contained) continue;
      const r = e.getBoundingClientRect();
      const margin = parseFloat(getComputedStyle(e).marginRight) || 0;
      if (r.width && r.right + Math.max(0, margin) > limit + 0.5) {
        culprits.push(`${e.tagName.toLowerCase()}.${[...e.classList].join('.')} (+${(r.right + Math.max(0, margin) - limit).toFixed(1)}px)`);
      }
    }
    const name = s === document.documentElement ? 'html' : `${s.tagName.toLowerCase()}.${[...s.classList].join('.')}`;
    out.push(`${name} is ${s.scrollWidth - s.clientWidth}px wider inside; ` + (culprits.slice(-3).join(', ') || 'nothing sticks out by its box: a margin, a pseudo-element or a shadow part'));
  }
  return out;
}"""


def states(page: Any, path: str) -> list[tuple[str, Any]]:
    """What to open on a page, as (name, action); an action that finds nothing to open is skipped."""

    def click(selector: str) -> Any:
        return lambda: page.evaluate("(s) => { const e = document.querySelector(s); if (!e) return false; e.click(); return true; }", selector)

    out: list[tuple[str, Any]] = [("as it opens", lambda: True)]
    if path == "/playground":
        out += [
            ("chat list toggled", click(".chat-header .list-toggle")),
            ("editor open", lambda: page.evaluate("""() => { const b = document.querySelector('.user-message .actions button:nth-child(2)'); if (!b) return false; b.click(); return true; }""")),
            ("chat menu open", click(".chat-header .menu-root md-icon-button")),
        ]
    out.append(("first menu open", click("main .menu-root md-icon-button, main .menu-root button")))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:10600", help="the server's address")
    parser.add_argument("--engine", action="append", choices=["chromium", "firefox"], help="a browser to check in (default: both)")
    parser.add_argument("--width", action="append", type=int, help="a window width to check at (default: 320 to 1600)")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("check_layout needs Playwright: pip install playwright && playwright install chromium firefox", file=sys.stderr)
        return 2

    found = 0
    with sync_playwright() as p:
        for engine in args.engine or ["chromium", "firefox"]:
            # Chromium hides scrollbars when headless; show them, as a desktop does.
            browser = getattr(p, engine).launch(**({"ignore_default_args": ["--hide-scrollbars"]} if engine == "chromium" else {}))
            for width in args.width or WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 860})
                page = context.new_page()
                for path in PAGES:
                    for name, action in states(page, path):
                        page.goto(f"{args.url.rstrip('/')}/#{path}")
                        page.wait_for_timeout(1200)
                        if not action():
                            continue
                        page.wait_for_timeout(600)
                        for line in page.evaluate(FIND):
                            found += 1
                            print(f"{engine} {width}px {path} ({name}): {line}")
                context.close()
            browser.close()
    print(f"{found} overflow(s)" if found else "no sideways overflow")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
