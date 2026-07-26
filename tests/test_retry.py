import pytest
from pydantic import BaseModel, ValidationError

from retry import external_call_retry, is_transient_error


class _Model(BaseModel):
    value: int


def test_transient_error_detected_by_message():
    assert is_transient_error(TimeoutError("request timed out")) is True
    assert is_transient_error(Exception("429 rate limit exceeded")) is True
    assert is_transient_error(Exception("503 Service Unavailable")) is True


def test_validation_error_not_transient():
    try:
        _Model(value="not an int")
    except ValidationError as exc:
        assert is_transient_error(exc) is False
    else:
        pytest.fail("expected ValidationError")


def test_generic_non_transient_error_not_retried_message():
    assert is_transient_error(ValueError("malformed request: missing field")) is False


def test_eventually_succeeds_after_transient_failures():
    calls = {"count": 0}

    @external_call_retry
    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("request timed out")
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 3


def test_does_not_retry_non_transient_failure():
    calls = {"count": 0}

    @external_call_retry
    def always_invalid():
        calls["count"] += 1
        raise ValueError("malformed request")

    with pytest.raises(ValueError):
        always_invalid()
    assert calls["count"] == 1
