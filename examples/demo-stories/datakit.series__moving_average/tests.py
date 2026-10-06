import pytest
from datakit.series import moving_average


def test_integer_series():
    assert moving_average([1, 2, 3, 4, 5], 2) == [1.5, 2.5, 3.5, 4.5]


def test_window_of_one_gives_floats():
    result = moving_average([3, 1, 4], 1)
    assert result == [3.0, 1.0, 4.0]
    assert all(type(x) is float for x in result)


def test_window_as_long_as_series():
    assert moving_average([2, 4, 6], 3) == [4.0]


def test_series_shorter_than_window():
    assert moving_average([1, 2], 3) == []
    assert moving_average([], 1) == []


def test_float_values():
    assert moving_average([0.1, 0.2, 0.3, 0.4], 2) == pytest.approx([0.15, 0.25, 0.35])


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], 0)
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], -3)


@pytest.mark.nz_workload
def test_workload_long_series():
    values = [((i * 37) % 101) / 10 + (i % 7) * 0.3 for i in range(5000)]
    result = moving_average(values, 200)
    assert len(result) == 4801
    assert result[0] == pytest.approx(5.8955)
    assert result[-1] == pytest.approx(5.913)
    assert sum(result) == pytest.approx(28325.9225)
