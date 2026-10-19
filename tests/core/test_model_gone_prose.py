"""When the number says "your request" and the body says "no such id".

The upstream record is dsh's `channel-pack/src/dead-model-store.ts`, which is
unusual in carrying a full page of measured misjudgements beside each pattern:
real sentences that were wrongly read as removal, and real removals that were
wrongly passed over. The clone is gitignored, so every case below is
transcribed rather than imported — and the whole set is locked rather than
spot-checked, because the interesting half of this table is the `false`
column. A spot check would find the obvious `model not found` and miss that
`model not available in your region` had quietly become a removal.

The corpus was run against dsh's shipped JavaScript before being written here
and agrees on all of it. Two lessons from that run are worth the lines:

* a caret does not mean the same thing in Python. `Pattern.match(text, pos)`
  still reads `^` as the start of the *string*, so anchoring the verdict with
  one makes it unreachable from every position but the first — the adjacency
  check then returns false everywhere while looking perfectly correct;
* splicing a bracketed character class into another class closes it early and
  demands a literal `]` from the message. Both failures are silent: the code
  runs, the tests that only check the obvious positive still pass, and the
  entire id-shaped branch is dead.
"""

from __future__ import annotations

import pytest

from hoshimi.core.failure import (
    MODEL_GONE_PROSE_STATUSES,
    MODEL_GONE_STATUSES,
    body_says_model_gone,
    is_model_gone,
)

# -- the measured corpus ----------------------------------------------------- #

# (message, expected). Every row is a real sentence from dsh's notes or its
# test corpus; the comment on each `false` is what it would cost to get it
# wrong.
GONE: list[tuple[str, bool]] = [
    # -- said outright ---------------------------------------------------- #
    ("model not found", True),
    ("model not found.", True),
    ("model not found, retry allowed", True),
    ("unknown model", True),
    ("no such model foo", True),
    ("no such model: gpt-4o", True),
    # -- an id, then the verdict, adjacent ------------------------------- #
    ("model a does not exist", True),
    # The dot: every id this gateway serves has one, and a window written as
    # "no sentence end between" stops at the first decimal point.
    ("The model gpt-5.6-astra does not exist", True),
    ("model deepseek-v4.1-flash 不存在", True),
    ("model deepseekv41flash does not exist", True),
    ('model "deepseek-v4.1-flash" does not exist', True),
    ("The model `gpt-5.6-astra` does not exist", True),
    ("model expired-v3 does not exist", True),
    # -- Chinese ---------------------------------------------------------- #
    ("该模型已下线", True),
    ("模型已下架", True),
    # -- must NOT be read as a removal ------------------------------------ #
    # A plan, a region, a reasoning flag: all three come back without touching
    # the model. Hiding one for a session over any of them is worse than
    # asking again.
    ("cline: model not supported in this plan", False),
    ("model not available in your region", False),
    ("the selected model is not available for reasoning", False),
    # Account complaints that happen to name a model.
    ("rate limit exceeded for model a", False),
    ("insufficient quota for model a", False),
    ("request unauthorized for model a", False),
    ("forbidden: model a", False),
    ("invalid api key", False),
    ("token expired for model a", False),
    ("the model lease expired", False),
    # A field name in telemetry, not a verdict: a comma after it means another
    # field follows.
    ("error_class=model_not_found, retry allowed", False),
    ("the model_not_found metric was not exported", False),
    ("model_not_found_reason=upstream", False),
    # Cross-sentence: `x` is a candidate id and "does not exist" is a verdict,
    # but nothing joins them. The `.` is both a decimal point and a full stop,
    # so a window cannot tell these apart — only adjacency can.
    ("model x is fine. the container image does not exist", False),
    ("the config file model list is fine; however this build does not exist", False),
    # An account complaint outranks a removal in the same message: the miss
    # costs one more round trip, the hit costs the caller a model they can no
    # longer see.
    ("模型 qfmodel 已下架，积分不予退还", False),
    # dsh's comment claims `\b` let this through; its shipped code says false.
    # Kept at the shipped answer — a miss is the safe direction, and an id
    # containing "quota" is a thing no lane here has.
    ("model-quota-v2 does not exist", False),
    # -- unknown to dsh's table as well ------------------------------------ #
    # Present to show where the table stops: this is not evidence of anything.
    ("The model claude-sonnet-4.5 was not found", False),
    ("没有找到该模型", False),
]


@pytest.mark.parametrize("message, expected", GONE, ids=[m[:40] for m, _ in GONE])
def test_the_body_verdict_matches_the_measured_table(message: str, expected: bool) -> None:
    assert body_says_model_gone(message) is expected


# -- the two questions, kept apart ------------------------------------------- #


def test_the_status_is_asked_first_and_never_overturned() -> None:
    """A body mentioning quota does not rename a 404."""
    assert is_model_gone(404) and is_model_gone(410)
    assert is_model_gone(404, "quota exceeded for this endpoint")
    # And a body saying "gone" does not invent a 404 out of a status that
    # answered about the account.
    assert not is_model_gone(401, "model not found")
    assert not is_model_gone(403, "model not found")
    assert not is_model_gone(429, "model not found")
    assert not is_model_gone(502, "model not found")


def test_the_body_is_only_asked_where_the_number_does_not_already_say_what_it_means() -> None:
    assert not is_model_gone(400) and not is_model_gone(422), "no body, no verdict"
    assert is_model_gone(400, "model not found")
    assert is_model_gone(422, "The model gpt-5.6-astra does not exist")
    # The request-shaped family as a whole, so the rule is one sentence.
    assert MODEL_GONE_PROSE_STATUSES == frozenset({400, 405, 406, 415, 418, 422, 451})
    assert MODEL_GONE_STATUSES == frozenset({404, 410})


def test_the_two_status_sets_do_not_overlap() -> None:
    """Otherwise one answer would be reachable through two different doors."""
    assert not (MODEL_GONE_STATUSES & MODEL_GONE_PROSE_STATUSES)


def test_an_empty_body_is_never_a_verdict() -> None:
    assert not body_says_model_gone("")
    assert not is_model_gone(400, "")


def test_an_account_verdict_is_still_reached_from_the_body() -> None:
    """The prose branch must not swallow the classification it sits above."""
    from hoshimi.core.providers.openai_compat import _classify

    with pytest.raises(Exception) as excinfo:
        _classify("lane", 400, "insufficient quota for this endpoint")
    assert type(excinfo.value).__name__ == "QuotaExceeded"


# -- the same verdict arriving inside a 200 stream --------------------------- #


def test_an_id_verdict_riding_a_200_stream_still_reaches_the_dead_store() -> None:
    """A station that has dropped an id says so on a status line or in a frame.

    dsh reads it in exactly this place (`trackStream`). If it is only ever
    yielded, the dead-model store never hears about it — the caller is handed
    the same refusal every turn, and the gateway relearns a fact it was
    already told.
    """
    from hoshimi.core.providers.base import ModelGone
    from hoshimi.core.providers.openai_compat import _classify_in_band

    with pytest.raises(ModelGone) as excinfo:
        _classify_in_band("lane", '{"error":{"message":"model not found"}}')
    assert excinfo.value.status == 404


def test_an_account_verdict_in_a_frame_is_still_an_account_verdict() -> None:
    """The frame path must not decide by prose alone where quota is meant to win."""
    from hoshimi.core.providers.base import QuotaExceeded
    from hoshimi.core.providers.openai_compat import _classify_in_band

    with pytest.raises(QuotaExceeded):
        _classify_in_band("lane", "insufficient quota for this endpoint")


def test_a_frame_that_says_nothing_is_still_passed_through_as_words() -> None:
    """An unrecognised frame is the station's own verdict, not an exception."""
    from hoshimi.core.providers.openai_compat import _classify_in_band

    assert _classify_in_band("lane", "something the table has never seen") is None

