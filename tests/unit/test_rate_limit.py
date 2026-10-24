from unittest.mock import patch

from app.core.rate_limit import RateLimiter


def test_limiter_blocks_after_limit_and_recovers_after_window() -> None:
    limiter = RateLimiter()
    with patch("app.core.rate_limit.time.monotonic", return_value=100.0):
        assert limiter.hit("login:1.2.3.4", 2, 60) is None
        assert limiter.hit("login:1.2.3.4", 2, 60) is None
        assert limiter.hit("login:1.2.3.4", 2, 60) == 60
        assert limiter.hit("login:5.6.7.8", 2, 60) is None
    with patch("app.core.rate_limit.time.monotonic", return_value=160.0):
        assert limiter.hit("login:1.2.3.4", 2, 60) is None
