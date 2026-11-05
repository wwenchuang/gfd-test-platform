"""Advisory response-time assessment, independent of functional verdicts."""


def evaluate_response_time_warning(threshold_ms, response):
    if threshold_ms is None:
        return {"status": "disabled", "duration_ms": None, "threshold_ms": None}
    duration = response.get("duration_ms") if isinstance(response, dict) else None
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        return {"status": "unknown", "duration_ms": None, "threshold_ms": threshold_ms}
    return {
        "status": "slow" if duration > threshold_ms else "normal",
        "duration_ms": duration,
        "threshold_ms": threshold_ms,
    }
