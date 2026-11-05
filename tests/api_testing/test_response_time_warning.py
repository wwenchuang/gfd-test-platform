import pytest

from task_server.api_testing.contracts.case import CasePayloadError, _parse_processing
from task_server.api_testing.response_time_warning import evaluate_response_time_warning


def test_warning_uses_main_response_and_does_not_change_case_status():
    assert evaluate_response_time_warning(1500, {"duration_ms": 1501}) == {
        "status": "slow", "duration_ms": 1501, "threshold_ms": 1500,
    }
    assert evaluate_response_time_warning(1500, {"duration_ms": 1500})["status"] == "normal"


def test_missing_response_is_unknown_and_unconfigured_is_disabled():
    assert evaluate_response_time_warning(1500, {})["status"] == "unknown"
    assert evaluate_response_time_warning(None, {"duration_ms": 2000})["status"] == "disabled"
    assert evaluate_response_time_warning(1500, {"duration_ms": True})["status"] == "unknown"


def test_processing_accepts_only_positive_integer_warning_threshold():
    assert _parse_processing({"response_time_warning_ms": 1200})["response_time_warning_ms"] == 1200
    assert "response_time_warning_ms" not in _parse_processing({})
    for invalid in (0, -1, True, "100", 1.5, 86400001):
        with pytest.raises(CasePayloadError):
            _parse_processing({"response_time_warning_ms": invalid})
