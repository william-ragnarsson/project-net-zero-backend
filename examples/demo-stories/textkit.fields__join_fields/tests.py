import pytest
from textkit.fields import join_fields


def test_plain_fields_are_written_as_they_are():
    assert join_fields(["2026-10-05", "paid", "42.50"]) == "2026-10-05,paid,42.50"


def test_field_with_separator_is_quoted():
    assert join_fields(["Smith, J.", "EUR"]) == '"Smith, J.",EUR'


def test_quote_characters_are_doubled():
    assert join_fields(['said "ok"', "x"]) == '"said ""ok""",x'


def test_line_break_is_quoted():
    assert join_fields(["two\nlines", "end"]) == '"two\nlines",end'


def test_custom_separator_and_quote():
    assert join_fields(["a;b", "it's", "c,d"], sep=";", quote="'") == "'a;b';'it''s';c,d"


def test_empty_list_and_empty_fields():
    assert join_fields([]) == ""
    assert join_fields(["", "x", ""]) == ",x,"


@pytest.mark.nz_workload
def test_workload_export_line():
    vocab = [
        "Acme Ltd",
        "Berlin",
        "42.50",
        "EUR",
        'said "ok"',
        "Smith, J.",
        "two\nlines",
        "2026-10-05",
        "paid",
        "",
    ]
    fields = [vocab[(i * 7) % 10] + str(i % 97) for i in range(80000)]
    line = join_fields(fields)
    assert len(line) == 799749
    assert line.count('"') == 80000
    assert line.startswith('Acme Ltd0,2026-10-051,"said ""ok""2",Berlin3,paid4,"Smith, J.5",')
    assert line.endswith(',69,"two\nlines70",EUR71')
