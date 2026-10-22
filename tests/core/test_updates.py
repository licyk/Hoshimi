"""The background update check: what counts as newer, and what a failure leaves behind."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from hoshimi.core.updates import UpdateChecker, is_newer, latest_release, release_version


def _document(*versions: str, yanked: tuple[str, ...] = (), info: str | None = None) -> dict[str, Any]:
    return {
        "info": {"version": info or versions[-1]},
        "releases": {v: [{"filename": f"hoshimi-{v}.tar.gz", "yanked": v in yanked}] for v in versions},
    }


def _checker(handler: Any, current: str = "0.3.4") -> UpdateChecker:
    return UpdateChecker(current=current, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("text", "parsed"),
    [("0.3.4", (0, 3, 4)), ("v1.2", (1, 2)), (" 10.0.1 ", (10, 0, 1)), ("1.0rc1", None), ("0.4.dev2", None), ("", None), (None, None)],
)
def test_release_version_reads_final_releases_only(text: object, parsed: tuple[int, ...] | None) -> None:
    assert release_version(text) == parsed


def test_is_newer_compares_numerically() -> None:
    assert is_newer("0.3.10", "0.3.9")
    assert is_newer("0.4", "0.3.4")
    assert not is_newer("0.3.0", "0.3")
    assert not is_newer("0.3.4", "0.3.4")
    assert not is_newer("0.3.3", "0.3.4")
    # A version that cannot be read is never offered as an update.
    assert not is_newer("0.5.0rc1", "0.3.4")
    assert not is_newer("0.5.0", "dev")


def test_latest_release_skips_pre_releases_and_yanked_releases() -> None:
    assert latest_release(_document("0.3.4", "0.3.10", "0.3.9")) == "0.3.10"
    assert latest_release(_document("0.3.4", "0.4.0rc1", info="0.4.0rc1")) == "0.3.4"
    assert latest_release(_document("0.3.4", "0.3.5", yanked=("0.3.5",))) == "0.3.4"
    assert latest_release({"info": {"version": "0.3.4"}}) == "0.3.4"
    assert latest_release({"info": {}}) is None
    assert latest_release([]) is None


def test_a_newer_release_is_reported() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_document("0.3.4", "0.3.5"))

    checker = _checker(handler)
    assert checker.state.status == "checking"
    state = checker.check_now()
    assert (state.status, state.current, state.latest) == ("available", "0.3.4", "0.3.5")
    assert state.checked_at is not None
    assert str(seen[0].url) == "https://pypi.org/pypi/hoshimi/json"


def test_the_running_release_is_current() -> None:
    state = _checker(lambda _: httpx.Response(200, json=_document("0.3.3", "0.3.4"))).check_now()
    assert (state.status, state.latest) == ("current", "0.3.4")


def test_a_failure_without_an_answer_says_so() -> None:
    state = _checker(lambda _: httpx.Response(503)).check_now()
    assert (state.status, state.latest) == ("failed", None)


def test_a_failure_keeps_the_last_good_answer() -> None:
    answers = iter([httpx.Response(200, json=_document("0.3.5")), httpx.Response(200, text="not json")])
    checker = _checker(lambda _: next(answers))
    first = checker.check_now()
    assert checker.check_now() == first
    assert checker.state.status == "available"


def test_start_checks_in_the_background_and_stop_ends_it() -> None:
    checker = _checker(lambda _: httpx.Response(200, json=_document("0.3.4")))
    checker.start()
    try:
        for _ in range(200):
            if checker.state.status != "checking":
                break
            time.sleep(0.01)
        assert checker.state.status == "current"
    finally:
        checker.stop()
    assert checker._thread is None
