"""``apply_patch``: Codex's file-oriented patch format, parsed and applied.

Ported from OpenAI Codex (``codex-rs/apply-patch``: ``parser.rs``,
``streaming_parser.rs``, ``seek_sequence.rs``, ``file_update.rs``,
``text_file.rs``, ``lib.rs``), licensed under the Apache License 2.0; the
grammar, the error texts and the summary are Codex's, so a model that knows
the tool there knows it here. A patch looks like::

    *** Begin Patch
    *** Add File: hello.txt
    +Hello world
    *** Update File: src/app.py
    *** Move to: src/main.py
    @@ def greet():
    -print("Hi")
    +print("Hello, world!")
    *** Delete File: obsolete.txt
    *** End Patch

Unlike the Codex library (which writes hunk by hunk), :func:`apply_patch`
works out every file's new contents first, as Codex's own tool does: a patch
whose second hunk does not apply leaves the first file untouched. Line endings
are kept (a CRLF file stays CRLF); a file always ends with a line ending.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

BEGIN_PATCH_MARKER = "*** Begin Patch"
END_PATCH_MARKER = "*** End Patch"
ADD_FILE_MARKER = "*** Add File: "
DELETE_FILE_MARKER = "*** Delete File: "
UPDATE_FILE_MARKER = "*** Update File: "
MOVE_TO_MARKER = "*** Move to: "
EOF_MARKER = "*** End of File"
CHANGE_CONTEXT_MARKER = "@@ "
EMPTY_CHANGE_CONTEXT_MARKER = "@@"

_HEADERS = "Valid hunk headers: '*** Add File: {path}', '*** Delete File: {path}', '*** Update File: {path}'"
_UNEXPECTED = "Unexpected line found in update hunk: '{line}'. Every line should start with ' ' (context line), '+' (added line), or '-' (removed line)"


class PatchError(Exception):
    """A patch that cannot be parsed or applied; the message is what the model is told."""


class ParseError(PatchError):
    pass


def _invalid_patch(message: str) -> ParseError:
    return ParseError(f"invalid patch: {message}")


def _invalid_hunk(message: str, line_number: int) -> ParseError:
    return ParseError(f"invalid hunk at line {line_number}, {message}")


@dataclass
class UpdateChunk:
    change_context: str | None = None
    """One line (usually a class or function definition) that the chunk follows."""
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    context_line_indices: list[tuple[int, int]] = field(default_factory=list)
    """Where the context lines sit in ``old_lines`` and ``new_lines``, so they keep their own line endings."""
    is_end_of_file: bool = False

    def push_context_line(self, line: str) -> None:
        self.context_line_indices.append((len(self.old_lines), len(self.new_lines)))
        self.old_lines.append(line)
        self.new_lines.append(line)

    @property
    def empty(self) -> bool:
        return not self.old_lines and not self.new_lines


@dataclass
class AddFile:
    path: str
    contents: str = ""


@dataclass
class DeleteFile:
    path: str


@dataclass
class UpdateFile:
    path: str
    move_path: str | None = None
    chunks: list[UpdateChunk] = field(default_factory=list)


Hunk = AddFile | DeleteFile | UpdateFile


# -- parsing ------------------------------------------------------------------ #
def parse_patch(patch: str) -> list[Hunk]:
    """The hunks of ``patch``; a patch wrapped in a ``<<EOF`` heredoc is unwrapped first (Codex's lenient mode)."""
    lines = patch.strip().split("\n")
    try:
        _check_boundaries(lines)
    except ParseError:
        if len(lines) >= 4 and lines[0] in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].endswith("EOF"):
            lines = lines[1:-1]
            _check_boundaries(lines)
        else:
            raise
    parser = _Parser()
    for line in lines[:-1]:
        parser.line(line.removesuffix("\r"))
    parser.end()
    return parser.finish()


def _check_boundaries(lines: list[str]) -> None:
    first = lines[0].strip() if lines else None
    last = lines[-1].strip() if lines else None
    if first == BEGIN_PATCH_MARKER and last == END_PATCH_MARKER:
        return
    if first != BEGIN_PATCH_MARKER:
        raise _invalid_patch("The first line of the patch must be '*** Begin Patch'")
    raise _invalid_patch("The last line of the patch must be '*** End Patch'")


class _Parser:
    """Codex's line state machine: not started, in the patch, in an add, a delete or an update hunk, ended."""

    def __init__(self) -> None:
        self.mode = "not-started"
        self.hunks: list[Hunk] = []
        self.line_number = 0
        self.hunk_line_number = 0

    def finish(self) -> list[Hunk]:
        if self.mode != "ended":
            raise _invalid_patch("The last line of the patch must be '*** End Patch'")
        return self.hunks

    def end(self) -> None:
        """The end marker, padded with whitespace or not (the boundaries were checked)."""
        self.line_number += 1
        self._ensure_update_not_empty(END_PATCH_MARKER)
        self.mode = "ended"

    def _ensure_update_not_empty(self, line: str) -> None:
        last = self.hunks[-1] if self.hunks else None
        if not isinstance(last, UpdateFile):
            return
        if not last.chunks and self.mode == "update":
            raise _invalid_hunk(f"Update file hunk for path '{last.path}' is empty", self.hunk_line_number)
        if last.chunks and last.chunks[-1].empty:
            if line == END_PATCH_MARKER:
                raise _invalid_hunk("Update hunk does not contain any lines", self.line_number)
            raise _invalid_hunk(_UNEXPECTED.format(line=line), self.line_number)

    def _header(self, trimmed: str) -> bool:
        """Handle a hunk header or the end marker; False for any other line."""
        if trimmed == END_PATCH_MARKER:
            self._ensure_update_not_empty(trimmed)
            self.mode = "ended"
            return True
        for marker, make, mode in (
            (ADD_FILE_MARKER, lambda p: AddFile(p), "add"),
            (DELETE_FILE_MARKER, lambda p: DeleteFile(p), "delete"),
            (UPDATE_FILE_MARKER, lambda p: UpdateFile(p), "update"),
        ):
            if trimmed.startswith(marker):
                self._ensure_update_not_empty(trimmed)
                self.hunks.append(make(trimmed[len(marker):]))
                self.mode = mode
                if mode == "update":
                    self.hunk_line_number = self.line_number
                return True
        return False

    def line(self, line: str) -> None:
        self.line_number += 1
        trimmed = line.strip()
        if self.mode == "not-started":
            if trimmed != BEGIN_PATCH_MARKER:
                raise _invalid_patch("The first line of the patch must be '*** Begin Patch'")
            self.mode = "started"
        elif self.mode in ("started", "delete"):
            if not self._header(trimmed):
                raise _invalid_hunk(f"'{trimmed}' is not a valid hunk header. {_HEADERS}", self.line_number)
        elif self.mode == "add":
            if self._header(trimmed):
                return
            hunk = self.hunks[-1]
            if line.startswith("+") and isinstance(hunk, AddFile):
                hunk.contents += line[1:] + "\n"
                return
            raise _invalid_hunk(f"'{trimmed}' is not a valid hunk header. {_HEADERS}", self.line_number)
        elif self.mode == "update":
            self._update_line(line)
        elif trimmed:  # ended
            raise _invalid_patch("The last line of the patch must be '*** End Patch'")

    def _update_line(self, line: str) -> None:
        update_line = line.rstrip()
        if self._header(update_line):
            return
        hunk = self.hunks[-1]
        assert isinstance(hunk, UpdateFile)
        chunks = hunk.chunks
        last = chunks[-1] if chunks else None
        is_context_marker = update_line == EMPTY_CHANGE_CONTEXT_MARKER or update_line.startswith(CHANGE_CONTEXT_MARKER)
        if last is not None and last.is_end_of_file:
            if not update_line:
                return
            if not is_context_marker:
                raise _invalid_hunk(f"Expected update hunk to start with a @@ context marker, got: '{line}'", self.line_number)
        if not chunks and hunk.move_path is None and update_line.startswith(MOVE_TO_MARKER):
            hunk.move_path = update_line[len(MOVE_TO_MARKER):]
            return
        if is_context_marker and last is not None and last.empty:
            raise _invalid_hunk(_UNEXPECTED.format(line=line), self.line_number)
        if update_line == EMPTY_CHANGE_CONTEXT_MARKER:
            chunks.append(UpdateChunk())
            return
        if update_line.startswith(CHANGE_CONTEXT_MARKER):
            chunks.append(UpdateChunk(change_context=update_line[len(CHANGE_CONTEXT_MARKER):]))
            return
        if update_line == EOF_MARKER:
            if last is not None and last.empty:
                raise _invalid_hunk("Update hunk does not contain any lines", self.line_number)
            if last is not None:
                last.is_end_of_file = True
            return
        if not line or line[0] in " +-":
            if not chunks:
                chunks.append(UpdateChunk())
            chunk = chunks[-1]
            if not line:
                chunk.push_context_line("")
            elif line[0] == " ":
                chunk.push_context_line(line[1:])
            elif line[0] == "+":
                chunk.new_lines.append(line[1:])
            else:
                chunk.old_lines.append(line[1:])
            return
        if last is not None and not last.empty:
            raise _invalid_hunk(f"Expected update hunk to start with a @@ context marker, got: '{line}'", self.line_number)
        raise _invalid_hunk(_UNEXPECTED.format(line=line), self.line_number)


# -- finding where a chunk goes ------------------------------------------------- #
_PUNCTUATION = str.maketrans(
    {
        **dict.fromkeys(["‐", "‑", "‒", "–", "—", "―", "−"], "-"),
        **dict.fromkeys(["‘", "’", "‚", "‛"], "'"),
        **dict.fromkeys(["“", "”", "„", "‟"], '"'),
        **dict.fromkeys([" ", " ", " ", " ", " ", " ", " ", " ", " ", " ", " ", " ", "　"], " "),
    }
)


def _normalise(line: str) -> str:
    return line.strip().translate(_PUNCTUATION)


_PASSES: tuple[Callable[[str], str], ...] = (lambda s: s, str.rstrip, str.strip, _normalise)


def seek_sequence(lines: list[str], pattern: list[str], start: int, eof: bool) -> int | None:
    """Where ``pattern`` starts in ``lines`` at or after ``start``, or None.

    Tried with less and less strictness: exactly, then ignoring trailing
    whitespace, then surrounding whitespace, then also typographic dashes,
    quotes and spaces. ``eof`` looks at the end of the file first.
    """
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    search_start = max(len(lines) - len(pattern), start) if eof else start
    last = len(lines) - len(pattern)
    for fold in _PASSES:
        folded = [fold(p) for p in pattern]
        for i in range(search_start, last + 1):
            if all(fold(lines[i + k]) == folded[k] for k in range(len(pattern))):
                return i
    return None


# -- working out a file's new contents -------------------------------------------- #
Replacement = tuple[int, int, list[str]]


@dataclass
class _Line:
    text: str
    ending: str | None


class SourceFile:
    """A file's lines with their own endings; inserted lines take the file's first ending (LF when none)."""

    def __init__(self, contents: str) -> None:
        self.lines: list[_Line] = []
        preferred: str | None = None
        start = i = 0
        while i < len(contents):
            ch = contents[i]
            if ch == "\r" and contents.startswith("\r\n", i):
                ending = "\r\n"
            elif ch in "\r\n":
                ending = ch
            else:
                i += 1
                continue
            preferred = preferred or ending
            self.lines.append(_Line(contents[start:i], ending))
            i += len(ending)
            start = i
        if start < len(contents):
            self.lines.append(_Line(contents[start:], None))
        self.preferred = preferred or "\n"

    def texts(self) -> list[str]:
        return [line.text for line in self.lines]

    def apply(self, replacements: list[Replacement]) -> str:
        out: list[_Line] = []
        index = 0
        for start, old_len, new in replacements:
            out.extend(self.lines[index:start])
            out.extend(_Line(text, self.preferred) for text in new)
            index = start + old_len
        out.extend(self.lines[index:])
        return "".join(line.text + (line.ending or self.preferred) for line in out)


def compute_replacements(lines: list[str], path: str, chunks: list[UpdateChunk], starts: list[int] | None = None) -> list[Replacement]:
    """Where each chunk's lines go; ``starts``, when given, gets each chunk's first line in ``lines`` (from 0)."""
    replacements: list[Replacement] = []
    line_index = 0
    for chunk in chunks:
        if chunk.change_context is not None:
            found = seek_sequence(lines, [chunk.change_context], line_index, False)
            if found is None:
                raise PatchError(f"Failed to find context '{chunk.change_context}' in {path}")
            line_index = found + 1
        if not chunk.old_lines:
            replacements.append((len(lines), 0, list(chunk.new_lines)))
            if starts is not None:
                starts.append(len(lines))
            continue
        pattern, new = chunk.old_lines, chunk.new_lines
        found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
        if found is None and pattern[-1] == "":
            # The trailing empty line stands for the file's last line ending.
            pattern = pattern[:-1]
            if new and new[-1] == "":
                new = new[:-1]
            found = seek_sequence(lines, pattern, line_index, chunk.is_end_of_file)
        if found is None:
            raise PatchError(f"Failed to find expected lines in {path}:\n" + "\n".join(chunk.old_lines))
        if starts is not None:
            starts.append(found)
        # Context lines stay as they are in the file, with their own endings.
        old_start = new_start = 0
        for old_context, new_context in chunk.context_line_indices:
            if old_context >= len(pattern) or new_context >= len(new):
                break
            if old_start != old_context or new_start != new_context:
                replacements.append((found + old_start, old_context - old_start, new[new_start:new_context]))
            old_start, new_start = old_context + 1, new_context + 1
        if old_start != len(pattern) or new_start != len(new):
            replacements.append((found + old_start, len(pattern) - old_start, new[new_start:]))
        line_index = found + len(pattern)
    replacements.sort(key=lambda r: r[0])
    return replacements


def updated_contents(original: str, path: str, chunks: list[UpdateChunk], starts: list[int] | None = None) -> str:
    source = SourceFile(original)
    return source.apply(compute_replacements(source.texts(), path, chunks, starts))


def hunk_lines(chunks: list[UpdateChunk], starts: list[int]) -> list[tuple[int, int]]:
    """Where each chunk starts (from 1) in the file before and after, in the chunks' order: the after line counts
    what the chunks above it (in the file, not in the patch) added and removed."""
    after: dict[int, int] = {}
    offset = 0
    for i in sorted(range(len(chunks)), key=lambda i: starts[i]):
        after[i] = starts[i] + offset
        offset += len(chunks[i].new_lines) - len(chunks[i].old_lines)
    return [(starts[i] + 1, after[i] + 1) for i in range(len(chunks))]


def _counts(chunks: list[UpdateChunk]) -> tuple[int, int]:
    """The lines a file's chunks add and remove: context lines stand in both and count in neither."""
    context = sum(len(c.context_line_indices) for c in chunks)
    return sum(len(c.new_lines) for c in chunks) - context, sum(len(c.old_lines) for c in chunks) - context


# -- applying ------------------------------------------------------------------- #
@dataclass
class Change:
    """One file's part of a patch, worked out before anything is written."""

    kind: str
    """``add``, ``delete`` or ``update``."""
    path: str
    """As the patch names it."""
    target: Path
    contents: str = ""
    move_to: str | None = None
    move_target: Path | None = None
    old: str = ""
    added: int = 0
    removed: int = 0
    hunks: list[tuple[int, int]] = field(default_factory=list)
    """An update's chunks, in the patch's order: the line each starts at (from 1) before and after."""


@dataclass
class Applied:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["Success. Updated the following files:"]
        lines += [f"A {p}" for p in self.added] + [f"M {p}" for p in self.modified] + [f"D {p}" for p in self.deleted]
        return "\n".join(lines) + "\n"


def plan_patch(patch: str, resolve: Callable[[str], Path]) -> list[Change]:
    """Every change ``patch`` makes, worked out against the files as they are; nothing is written.

    ``resolve`` turns a path as the patch names it into the file it means (and
    raises :class:`PatchError` for one it may not touch).
    """
    hunks = parse_patch(patch)
    if not hunks:
        raise PatchError("No files were modified.")
    changes: list[Change] = []
    seen: set[Path] = set()

    def claim(path: Path, name: str) -> None:
        if path in seen:
            raise PatchError(f"multiple operations target {name}")
        seen.add(path)

    for hunk in hunks:
        target = resolve(hunk.path)
        claim(target, hunk.path)
        if isinstance(hunk, AddFile):
            if target.is_dir():
                raise PatchError(f"Failed to write file {hunk.path}: it is a directory")
            changes.append(Change("add", hunk.path, target, contents=hunk.contents, added=len(hunk.contents.splitlines())))
        elif isinstance(hunk, DeleteFile):
            if target.is_dir():
                raise PatchError(f"Failed to delete file {hunk.path}: it is a directory")
            if not target.is_file():
                raise PatchError(f"Failed to delete file {hunk.path}: no such file")
            old = _read(target, hunk.path, "delete")
            changes.append(Change("delete", hunk.path, target, old=old, removed=len(old.splitlines())))
        else:
            if not target.is_file():
                raise PatchError(f"Failed to read file to update {hunk.path}: no such file")
            old = _read(target, hunk.path, "update")
            starts: list[int] = []
            added, removed = _counts(hunk.chunks)
            contents = updated_contents(old, hunk.path, hunk.chunks, starts)
            change = Change("update", hunk.path, target, contents=contents, old=old, added=added, removed=removed, hunks=hunk_lines(hunk.chunks, starts))
            if hunk.move_path:
                change.move_to, change.move_target = hunk.move_path, resolve(hunk.move_path)
                if change.move_target != target:
                    claim(change.move_target, hunk.move_path)
                if change.move_target.is_dir():
                    raise PatchError(f"Failed to write file {hunk.move_path}: it is a directory")
            changes.append(change)
    return changes


def _read(path: Path, name: str, action: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise PatchError(f"Failed to read file to {action} {name}: it is not UTF-8 text") from None
    except OSError as exc:
        raise PatchError(f"Failed to read file to {action} {name}: {exc.strerror or exc}") from None


def commit(changes: list[Change]) -> Applied:
    """Write the changes :func:`plan_patch` worked out."""
    applied = Applied()
    for change in changes:
        try:
            if change.kind == "delete":
                change.target.unlink()
                applied.deleted.append(change.path)
                continue
            destination = change.move_target or change.target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(change.contents.encode("utf-8"))
            if change.move_target is not None and change.move_target != change.target:
                change.target.unlink()
            (applied.added if change.kind == "add" else applied.modified).append(change.path)
        except OSError as exc:
            raise PatchError(f"Failed to write file {change.move_to or change.path}: {exc.strerror or exc}") from None
    return applied


def apply_patch(patch: str, cwd: Path) -> Applied:
    """Apply ``patch`` with relative paths under ``cwd``; all or nothing as far as the patch itself goes."""
    return commit(plan_patch(patch, lambda name: cwd / name))
