"""
Tests fuer HNZ-001-0008: Rate-Limiting und Retry-Logik
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/llm-provider"))
import pytest


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Hilfsfunktionen ──────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

class FakeHTTPError(Exception):
    def __init__(self, status_code, headers=None):
        self.response = FakeResponse(status_code, headers)
        self.status_code = status_code

def fast_config(max_retries=2):
    return {"retry": {"max_retries": max_retries, "initial_delay_s": 0.001,
                      "backoff_factor": 1.0, "max_delay_s": 0.01,
                      "retry_on": [429, 500, 503]}}


# ─── Delay-Berechnung ─────────────────────────────────────────

def test_delay_exponential():
    from retry import _get_delay
    cfg = {"initial_delay_s": 1.0, "backoff_factor": 2.0, "max_delay_s": 60.0}
    assert _get_delay(1, cfg) == 1.0
    assert _get_delay(2, cfg) == 2.0
    assert _get_delay(3, cfg) == 4.0


def test_delay_max_cap():
    from retry import _get_delay
    cfg = {"initial_delay_s": 1.0, "backoff_factor": 10.0, "max_delay_s": 5.0}
    assert _get_delay(5, cfg) == 5.0


def test_delay_retry_after_header():
    from retry import _get_delay
    cfg = {"initial_delay_s": 1.0, "backoff_factor": 2.0, "max_delay_s": 60.0}
    assert _get_delay(1, cfg, retry_after=30) == 30.0


def test_delay_retry_after_capped_at_max():
    from retry import _get_delay
    cfg = {"initial_delay_s": 1.0, "backoff_factor": 2.0, "max_delay_s": 10.0}
    assert _get_delay(1, cfg, retry_after=999) == 10.0


# ─── Config ───────────────────────────────────────────────────

def test_default_config_used_when_no_retry_key():
    from retry import _get_retry_config, DEFAULT_RETRY_CONFIG
    result = _get_retry_config({})
    assert result == DEFAULT_RETRY_CONFIG


def test_yaml_config_overrides_defaults():
    from retry import _get_retry_config
    result = _get_retry_config({"retry": {"max_retries": 5}})
    assert result["max_retries"] == 5
    assert result["backoff_factor"] == 2.0  # default beibehalten


# ─── with_retry Verhalten ─────────────────────────────────────

def test_success_first_try():
    from retry import with_retry
    async def fn(): return "ok"
    result = run(with_retry(fn, {}))
    assert result == "ok"


def test_retry_on_retryable_status():
    from retry import with_retry
    calls = []
    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise FakeHTTPError(500)
        return "ok"
    result = run(with_retry(fn, fast_config(max_retries=3)))
    assert result == "ok"
    assert len(calls) == 3


def test_no_retry_on_404():
    from retry import with_retry
    calls = []
    async def fn():
        calls.append(1)
        raise FakeHTTPError(404)
    with pytest.raises(FakeHTTPError):
        run(with_retry(fn, fast_config()))
    assert len(calls) == 1  # kein Retry


def test_raise_retry_exhausted_after_max():
    from retry import with_retry, RetryExhausted
    async def fn():
        raise FakeHTTPError(500)
    with pytest.raises(RetryExhausted) as exc_info:
        run(with_retry(fn, fast_config(max_retries=2)))
    assert exc_info.value.attempts == 3


def test_raise_rate_limit_on_429():
    from retry import with_retry, RateLimitHit
    async def fn():
        raise FakeHTTPError(429)
    with pytest.raises(RateLimitHit):
        run(with_retry(fn, fast_config(max_retries=2)))


def test_rate_limit_tracker_incremented():
    from retry import with_retry, RateLimitHit
    tracker = []
    async def fn():
        raise FakeHTTPError(429)
    with pytest.raises(RateLimitHit):
        run(with_retry(fn, fast_config(max_retries=2), rate_limit_tracker=tracker))
    assert len(tracker) > 0  # mind. ein Eintrag


def test_no_retry_on_non_error():
    from retry import with_retry
    async def fn(): return 42
    result = run(with_retry(fn, fast_config()))
    assert result == 42
