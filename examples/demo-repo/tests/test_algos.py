from algos.graph import Graph
from algos.primes import primes_below
from algos.strings import levenshtein


def test_primes_below_twenty():
    assert primes_below(20) == [2, 3, 5, 7, 11, 13, 17, 19]


def test_levenshtein_kitten_sitting():
    assert levenshtein("kitten", "sitting") == 3


def test_shortest_path_lengths_on_a_path():
    g = Graph({"a": ["b"], "b": ["a", "c"], "c": ["b"]})
    assert g.shortest_path_lengths("a") == {"a": 0, "b": 1, "c": 2}
