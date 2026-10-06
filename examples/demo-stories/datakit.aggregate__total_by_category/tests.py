import pytest
from datakit.aggregate import total_by_category


def test_empty_rows():
    assert total_by_category([]) == {}


def test_totals_in_first_seen_order():
    rows = [
        {"category": "rent", "amount": 90000},
        {"category": "food", "amount": 1250},
        {"category": "rent", "amount": 5000},
        {"category": "travel", "amount": 300},
        {"category": "food", "amount": 750},
    ]
    result = total_by_category(rows)
    assert list(result.items()) == [("rent", 95000), ("food", 2000), ("travel", 300)]


def test_custom_amount_key():
    rows = [
        {"category": "a", "cents": 5, "amount": 999},
        {"category": "b", "cents": 7, "amount": 999},
        {"category": "a", "cents": 1, "amount": 999},
    ]
    assert total_by_category(rows, amount_key="cents") == {"a": 6, "b": 7}


def test_refunds_can_cancel_out():
    rows = [
        {"category": "books", "amount": 1500},
        {"category": "books", "amount": -1500},
        {"category": "music", "amount": -200},
    ]
    assert list(total_by_category(rows).items()) == [("books", 0), ("music", -200)]


@pytest.mark.nz_workload
def test_workload_ledger():
    rows = [
        {"category": f"cat{(i * 7) % 40:02d}", "amount": (i * 37) % 1000, "id": i}
        for i in range(5000)
    ]
    result = total_by_category(rows)
    assert len(result) == 40
    assert sum(result.values()) == 2497500
    assert list(result.items())[:3] == [("cat00", 60000), ("cat07", 64625), ("cat14", 64250)]
    assert result["cat39"] == 63625
