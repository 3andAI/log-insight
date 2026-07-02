import pytest

from log_insight.normalize import (
    LEVEL_DEBUG,
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_UNKNOWN,
    LEVEL_WARNING,
    level_from_priority,
)


@pytest.mark.parametrize(
    "priority,expected",
    [
        (0, LEVEL_ERROR),
        (1, LEVEL_ERROR),
        (2, LEVEL_ERROR),
        (3, LEVEL_ERROR),
        (4, LEVEL_WARNING),
        (5, LEVEL_INFO),
        (6, LEVEL_INFO),
        (7, LEVEL_DEBUG),
        # journald emits PRIORITY as a decimal string.
        ("0", LEVEL_ERROR),
        ("3", LEVEL_ERROR),
        ("6", LEVEL_INFO),
        ("7", LEVEL_DEBUG),
    ],
)
def test_priority_maps_to_level(priority, expected):
    assert level_from_priority(priority) == expected


@pytest.mark.parametrize("priority", [None, "", "x", "not-a-number", 8, -1, 99])
def test_missing_or_out_of_range_priority_is_unknown(priority):
    assert level_from_priority(priority) == LEVEL_UNKNOWN
