from datetime import datetime, timezone

import pytest

from revenueos.sla import (AMBER, GREEN, RED, business_days_between, health_for_age,
                           shift_back_business_days)


def d(day, hour=12):
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)


def test_one_weekday():
    assert business_days_between(d(2), d(3)) == pytest.approx(1.0)  # Mon 12:00 -> Tue 12:00


def test_weekend_is_skipped():
    assert business_days_between(d(6), d(9)) == pytest.approx(1.0)  # Fri 12:00 -> Mon 12:00


def test_pure_weekend_is_zero():
    assert business_days_between(d(7, 0), d(8, 23)) == 0.0


def test_end_before_start_is_zero():
    assert business_days_between(d(3), d(2)) == 0.0


@pytest.mark.parametrize("age,expected", [
    (0.0, GREEN), (0.99, GREEN), (1.0, AMBER), (1.99, AMBER), (2.0, RED), (6.8, RED)])
def test_health_thresholds(age, expected):
    assert health_for_age(age) == expected


def test_shift_back_simple_and_over_weekend():
    assert shift_back_business_days(d(3), 1.0) == d(2)
    assert shift_back_business_days(d(9), 1.0) == d(6)


@pytest.mark.parametrize("days", [0.5, 1.5, 3, 7])
def test_round_trip(days):
    now = d(5)
    assert business_days_between(shift_back_business_days(now, days), now) == pytest.approx(days)
