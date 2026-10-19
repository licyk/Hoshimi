"""Tables, ``--json`` output, shared helpers and progress for the command line."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from hoshimi.core.context import Services

console = Console()
err_console = Console(stderr=True)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return json.loads(value.json())
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def print_json(value: Any) -> None:
    """Print the same shape the API returns."""
    sys.stdout.write(json.dumps(to_jsonable(value), indent=2, ensure_ascii=False) + "\n")


def print_table(title: str | None, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    table = Table(title=title, show_lines=False, header_style="bold")
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*["" if v is None else str(v) for v in row])
    console.print(table)


@contextmanager
def open_services() -> Generator[Services]:
    """Build the services for one command and close them afterwards."""
    from hoshimi.core.context import build_services

    services: Services = build_services()
    try:
        yield services
    finally:
        services.close()


def short(text: str | None, width: int = 60) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"
