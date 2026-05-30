from datetime import datetime, timezone
from app.retry_strategy import compute_next_attempt, RetryStrategy


def test_exponential_backoff_grows():
    # Formula: base * 2^max(attempt-1, 0)
    # attempt 0 → 30*1=30s, attempt 1 → 30*1=30s, attempt 2 → 30*2=60s
    t0, s0 = compute_next_attempt(0, 500, {}, None, base_seconds=30)
    t2, s2 = compute_next_attempt(2, 500, {}, None, base_seconds=30)
    t4, s4 = compute_next_attempt(4, 500, {}, None, base_seconds=30)
    assert s0 == RetryStrategy.EXPONENTIAL
    assert s2 == RetryStrategy.EXPONENTIAL
    assert t2 > t0   # attempt 2 (60s) > attempt 0 (30s)
    assert t4 > t2   # attempt 4 (240s) > attempt 2 (60s)


def test_4xx_no_retry():
    _, strategy = compute_next_attempt(0, 404, {}, None)
    assert strategy == RetryStrategy.NO_RETRY

    _, strategy = compute_next_attempt(0, 400, {}, None)
    assert strategy == RetryStrategy.NO_RETRY


def test_429_respects_retry_after_header():
    _, strategy = compute_next_attempt(0, 429, {"retry-after": "120"}, None)
    assert strategy == RetryStrategy.RESPECT_RETRY_AFTER


def test_503_uses_long_backoff():
    _, strategy = compute_next_attempt(0, 503, {}, None)
    assert strategy == RetryStrategy.LONG


def test_network_error_uses_fast_retry():
    _, strategy = compute_next_attempt(0, None, {}, "ConnectionError")
    assert strategy == RetryStrategy.FAST


def test_backoff_capped_at_3600():
    # attempt 20 — exponential would overflow without cap
    t, _ = compute_next_attempt(20, 500, {}, None, base_seconds=30)
    now = datetime.now(timezone.utc)
    diff = (t - now).total_seconds()
    assert diff <= 3600 + 5  # allow tiny clock skew
