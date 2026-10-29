"""Capacity survives stream lifetimes, probes, cancellation and reconfiguration."""

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from hoshimi.core.net.admission import AdmissionCancelled, AdmissionDelayed, ProviderAdmission
from hoshimi.core.probe import STATE_THROTTLED, _clone, state_from_error
from hoshimi.core.providers.base import Account, ProviderError, QuotaExceeded, RequestThrottled
from hoshimi.core.providers.opencode import OpencodeProvider
from hoshimi.core.registry import Registry


def answer(_account, _model, _body):
    yield {"type": "text_delta", "text": "ok"}


def test_probe_and_chat_share_slot_until_stream_closes():
    with httpx.Client() as client:
        provider = OpencodeProvider(client, [Account({"id": "a"})], {"admission": {"max_concurrency": 1, "queue_timeout_seconds": 0}})
        probe = _clone(provider, client)
        stream = provider.stream_using("m", {}, answer)
        next(stream)
        try:
            with pytest.raises(RequestThrottled) as caught:
                list(probe.stream_using("other", {}, answer))
            assert state_from_error(caught.value)[0] == STATE_THROTTLED
            assert probe.cooler is provider.cooler
        finally:
            stream.close()
        assert list(probe.stream_using("other", {}, answer)) == [{"type": "text_delta", "text": "ok"}]
        assert provider.admission.active == 0


def test_rebuilding_options_keeps_active_capacity_and_cooldowns():
    accounts = [{"id": "a", "api_key": "key"}]
    options = {"admission": {"max_concurrency": 1, "queue_timeout_seconds": 0}}
    with httpx.Client() as client:
        provider = OpencodeProvider(client, [Account(accounts[0])], options)
        registry = Registry({"opencode": provider}, client=client)
        registry._inputs = {"opencode": (accounts, options)}
        provider.admission.acquire(("a", "m"))
        provider.admission.defer(("a", "limited"), 60)
        registry.rebuild({"opencode": accounts}, {"opencode": {**options, "model_protocols": {"m": "chat"}}})
        replacement = registry.providers["opencode"]
        assert replacement is not provider
        assert replacement.admission is provider.admission
        with pytest.raises(RequestThrottled):
            list(replacement.stream_using("m", {}, answer))
        provider.admission.release()
        with pytest.raises(RequestThrottled) as caught:
            list(replacement.stream_using("limited", {}, answer))
        assert caught.value.retry_after > 59
        assert list(replacement.stream_using("m", {}, answer))


def test_cancel_while_queued_does_not_dispatch_or_take_a_slot():
    gate = ProviderAdmission({"max_concurrency": 1, "queue_timeout_seconds": 10})
    gate.acquire(("a", "m"))
    cancel = threading.Event()
    started = threading.Event()

    def queued():
        started.set()
        gate.acquire(("b", "m"), cancel=cancel)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(queued)
        assert started.wait(1)
        cancel.set()
        with pytest.raises(AdmissionCancelled):
            future.result(timeout=1)
    assert gate.active == 1
    gate.release()


def test_lower_concurrency_limit_drains_existing_streams():
    gate = ProviderAdmission({"max_concurrency": 2, "queue_timeout_seconds": 0})
    gate.acquire(("a", "m"))
    gate.acquire(("b", "m"))
    gate.reconfigure({"max_concurrency": 1, "queue_timeout_seconds": 0})
    gate.release()
    with pytest.raises(AdmissionDelayed):
        gate.acquire(("c", "m"))
    gate.release()
    gate.acquire(("c", "m"))
    gate.release()
    assert gate.active == 0


def test_native_cancel_is_not_retried_or_marked_as_quota():
    with httpx.Client() as client:
        provider = OpencodeProvider(client, [Account({"id": "a"})])
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ProviderError) as caught:
            list(provider.stream_using("m", {}, answer, cancel=cancel))
        assert caught.value.code == "cancelled"
        assert provider.cooler.snapshot() == []
        assert provider.admission.active == 0


@pytest.mark.parametrize(("scope", "blocked", "allowed"), [
    (("a", ""), ("a", "different"), ("b", "m")),
    (("", "m"), ("b", "m"), ("a", "different")),
    (("a", "m"), ("a", "m"), ("a", "different")),
])
def test_wait_scopes_do_not_block_unrelated_work(scope, blocked, allowed):
    gate = ProviderAdmission({"queue_timeout_seconds": 0})
    gate.defer(scope, 60)
    with pytest.raises(AdmissionDelayed):
        gate.acquire(blocked)
    gate.acquire(allowed)
    gate.release()


def test_queued_request_rechecks_quota_before_dispatch(monkeypatch):
    with httpx.Client() as client:
        provider = OpencodeProvider(client, [Account({"id": "a"})], {"admission": {"max_concurrency": 1}})
        gate = provider.admission
        gate.acquire(("a", "m"))
        started = threading.Event()
        acquire = gate.acquire

        def waiting(*args, **kwargs):
            started.set()
            return acquire(*args, **kwargs)

        monkeypatch.setattr(gate, "acquire", waiting)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(lambda: list(provider.stream_using("m", {}, answer)))
            assert started.wait(1)
            provider.cooler.hold("opencode", "a", 60, model="m", reason="rate_limited")
            gate.release()
            with pytest.raises(QuotaExceeded):
                pending.result(timeout=1)
        assert gate.active == 0
