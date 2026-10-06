import pytest
from datakit.aggregate import total_by_category
from datakit.series import moving_average


def test_total_by_category():
    rows = [
        {"category": "food", "amount": 1250},
        {"category": "rent", "amount": 90000},
        {"category": "food", "amount": 830},
    ]
    assert total_by_category(rows) == {"food": 2080, "rent": 90000}


def test_moving_average_rejects_empty_window():
    with pytest.raises(ValueError):
        moving_average([1.0, 2.0], 0)
