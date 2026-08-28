"""Shared API case classifications used by services and execution gates."""

import re


_ONE_TIME_PATTERN = re.compile(r"\bone[- ]time\b")


def is_one_time_case(case_name="", group_name="", tags=()):
    text = " ".join(
        str(value or "")
        for value in (case_name, group_name, *(tags or ()))
    ).lower()
    return "一次性" in text or _ONE_TIME_PATTERN.search(text) is not None
