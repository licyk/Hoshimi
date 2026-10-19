"""Build the web UI, then the wheel and the source distribution.

    python scripts/build_wheel.py [--ci] [--keep-web-dist] [--outdir DIR]

Setuptools packages only the files that exist when it runs, so the web UI must be built first. A
bare ``python -m build`` can therefore produce a package without a web UI.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "hoshimi" / "webui"


def run(command: Sequence[str], cwd: Path = ROOT) -> None:
    """Run one command, echoing it first. Raises SystemExit with its status on failure."""
    print(f"\033[36m$ {' '.join(str(c) for c in command)}\033[0m", file=sys.stderr, flush=True)
    status = subprocess.call([str(c) for c in command], cwd=cwd)
    if status != 0:
        raise SystemExit(status)


def build_web(ci: bool) -> None:
    if shutil.which("bun") is None:
        raise SystemExit("bun is needed to build the web UI. Install it from https://bun.sh")
    run(["bun", "install", "--frozen-lockfile"], cwd=WEB)
    # CI runs type-check and tests separately; the package build then only bundles.
    run(["bun", "run", "build:only" if ci else "build"], cwd=WEB)


def build_package(outdir: Path) -> None:
    run([sys.executable, "-m", "build", "--outdir", str(outdir), str(ROOT)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/build_wheel.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ci", action="store_true", default=bool(os.environ.get("CI")), help="skip the web type-check and keep the built web UI")
    parser.add_argument("--keep-web-dist", action="store_true", default=bool(os.environ.get("KEEP_WEB_DIST")), help="keep hoshimi/webui/dist afterwards")
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist", help="where to write the wheel and sdist (default: dist/)")
    args = parser.parse_args(argv)

    build_web(args.ci)
    try:
        build_package(args.outdir)
    finally:
        if not args.ci and not args.keep_web_dist:
            # Leave a local checkout as it was.
            shutil.rmtree(WEB / "dist", ignore_errors=True)
    built = sorted(path.name for path in args.outdir.glob("hoshimi-*"))
    print("\nBuilt: " + ", ".join(built) if built else "\nNothing was built", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
