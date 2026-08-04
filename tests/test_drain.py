"""Graceful drain: a COOPERATIVE restart (Ctrl+C on the server window, or POST
/drain via restart-adam / the updater) finishes the in-flight turn instead of
killing it. Covers the wait-loop, the is_draining gate, the /ask_async 503 refusal
while draining, and the /drain response shape — WITHOUT firing the real os._exit.

A hard window-close / taskkill / reboot can't be intercepted and is handled by the
recoverable "restarted mid-task" message (see test_job_store.py + KNOWN_ISSUES).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ADAM_TOKEN", "test-token-drain-00000000000000000000")

import config  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app, raise_server_exceptions=False)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


def _reset() -> None:
    """Return the module drain state to rest between cases (the real reset is a
    process exit; tests share one process, so undo it by hand)."""
    server._DRAINING = False
    server.RUNNING_PROCS.clear()


def test_is_draining_default_false():
    _reset()
    assert server.is_draining() is False
    assert server.inflight_count() == 0


def test_drain_returns_immediately_when_idle():
    _reset()
    res = asyncio.run(server.drain_inflight(max_wait=5))
    assert res["drained"] is True
    assert res["started_with"] == 0
    # The gate flips even with nothing to wait for, so a racing new turn is refused.
    assert server.is_draining() is True
    _reset()


def test_drain_times_out_on_a_stuck_worker():
    _reset()
    server.RUNNING_PROCS["jobX"] = object()   # a live worker that never clears
    res = asyncio.run(server.drain_inflight(max_wait=1))
    assert res["drained"] is False
    assert res["left_running"] == 1
    _reset()


def test_drain_completes_when_the_worker_clears_midwait():
    _reset()
    server.RUNNING_PROCS["jobY"] = object()

    async def scenario():
        async def clear_soon():
            await asyncio.sleep(0.6)
            server.RUNNING_PROCS.clear()   # the turn finishes while we wait
        asyncio.create_task(clear_soon())
        return await server.drain_inflight(max_wait=10)

    res = asyncio.run(scenario())
    assert res["drained"] is True
    assert res["started_with"] == 1
    assert res["left_running"] == 0
    _reset()


def test_ask_async_refused_while_draining():
    _reset()
    server._DRAINING = True
    try:
        # The guard fires before any job_store write, so this touches no DB.
        r = client.post("/ask_async", headers=AUTH,
                        json={"message": "hello", "mode": "code"})
        assert r.status_code == 503
        assert "restart" in r.text.lower()
    finally:
        _reset()


def test_drain_endpoint_response_shape(monkeypatch):
    _reset()
    # The real endpoint schedules os._exit(0); stub it so this test process lives.
    monkeypatch.setattr(server, "begin_drain_and_exit", lambda max_wait=None: 3)
    r = client.post("/drain", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["draining"] is True
    assert body["running_jobs"] == 3
    assert body["max_wait_s"] == config.DRAIN_MAX_WAIT_SECONDS
    _reset()


def test_drain_endpoint_requires_token():
    _reset()
    r = client.post("/drain")   # no Authorization header
    assert r.status_code == 403
    _reset()


def test_ratelimited_dict_endpoint_injects_headers_not_500():
    """Regression: a @limiter.limit route that RETURNS A DICT must carry a
    `response: Response` param, or slowapi's header injection raises 500 on the
    success path (headers_enabled=True + the middleware exempts decorated routes,
    so the decorator is the only injector). This shipped broken in v0.9.54 and
    took out /ask, /ask_async, /upload, and /clientlog. /clientlog is the cheap
    canary — no LLM, no side effects beyond a log line."""
    _reset()
    r = client.post("/clientlog", headers=AUTH, json={"reason": "probe", "detail": "x"})
    assert r.status_code == 200, f"decorated dict endpoint 500'd (missing response param?): {r.text}"
    assert r.json() == {"ok": True}
    # The whole point of headers_enabled: the limit surfaces on the response.
    assert "X-RateLimit-Limit" in r.headers, "rate-limit headers not injected on a decorated route"
    _reset()
