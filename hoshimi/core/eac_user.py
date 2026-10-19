"""The EAC co-paid lane per-install GitHub authorization.

A signing gateway in front of the relay still needs ``x-ofm-user`` to know
which install is asking, and that headers value is a per-user GitHub
authorization token the install collects itself. It is stored here, beside
the rest of Hoshimis state under the platform data directory, owner
read/write only, written through a temp file + rename so a crash mid-write
cannot leave a half-token on disk.

The credential is owned by the lane, not by any one provider account: a
worker-mode seal and a direct-mode account can share the same token, so
this file holds it independently of ``config.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from hoshimi.core.paths import default_data_dir

EAC_USER_FILE = "eac-user.json"


def eac_user_path(data_dir: Path | str | None = None) -> Path:
    """Absolute path to the EAC user authorization file."""
    base_default = default_data_dir()
    base = Path(data_dir) if data_dir is not None else base_default
    return base.expanduser().resolve() / EAC_USER_FILE


def read_eac_user(path: Path | str | None = None) -> dict[str, Any] | None:
    """Read the stored authorization, or None when this install has none.

    The pre-login state is ``None`` (the file does not exist or holds an
    unreadable payload); the post-login state is a populated record. A token
    that has been forgotten on logout returns to ``None`` too.
    """
    file = Path(path) if path is not None else eac_user_path()
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        return None
    login = payload.get("login") if isinstance(payload.get("login"), str) else ""
    avatar = payload.get("avatar") if isinstance(payload.get("avatar"), str) else ""
    raw_saved = payload.get("savedAt")
    saved_at = 0
    if isinstance(raw_saved, (int, float)) and not isinstance(raw_saved, bool):
        saved_at = int(raw_saved)
    return {
        "token": token,
        "login": login,
        "avatar": avatar,
        "savedAt": saved_at,
    }


def _restrict_permissions(path: Path) -> None:
    """Owner read/write only, best effort.

    A read-only home must not break the lanes request path; the caller
    surfaces the failure where a user can see it. A filesystem that cannot
    apply mode bits (Windows, some network mounts) is not a reason to fail
    a save: the keys are inside the users own directory either way.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def write_eac_user(record: dict[str, Any], path: Path | str | None = None) -> bool:
    """Persist a freshly collected authorization.

    Returns True on success. A failed write is silent at the call site:
    the caller surfaces the verdict where a user can see it, and a read-only
    home must not break the lanes normal request path.
    """
    token = record.get("token")
    if not isinstance(token, str) or not token:
        return False
    target = Path(path) if path is not None else eac_user_path()
    target = target.expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{EAC_USER_FILE}.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                payload = {
                    "token": token,
                    "login": record.get("login", ""),
                    "avatar": record.get("avatar", ""),
                    "savedAt": int(record.get("savedAt") or 0) or _now_ms(),
                }
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, target)
        except OSError:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError:
        return False
    _restrict_permissions(target)
    return True


def clear_eac_user(path: Path | str | None = None) -> bool:
    """Forget the local authorization. The server-side token is revoked separately."""
    target = Path(path) if path is not None else eac_user_path()
    target = target.expanduser()
    try:
        target.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _now_ms() -> int:
    import time as _time

    return int(_time.time() * 1000)
