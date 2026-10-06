import pytest
from algos.graph import Graph


def grid(width, height):
    adjacency = {}
    for y in range(height):
        for x in range(width):
            node = y * width + x
            neighbours = []
            if x > 0:
                neighbours.append(node - 1)
            if x < width - 1:
                neighbours.append(node + 1)
            if y > 0:
                neighbours.append(node - width)
            if y < height - 1:
                neighbours.append(node + width)
            adjacency[node] = neighbours
    return Graph(adjacency)


def test_isolated_source():
    assert Graph({"a": []}).shortest_path_lengths("a") == {"a": 0}
    assert Graph().shortest_path_lengths("x") == {"x": 0}


def test_directed_chain_skips_unreachable():
    graph = Graph({"a": ["b"], "b": ["c"], "c": [], "z": ["a"]})
    assert graph.shortest_path_lengths("a") == {"a": 0, "b": 1, "c": 2}
    assert graph.shortest_path_lengths("c") == {"c": 0}


def test_shortest_not_first_found():
    graph = Graph({"a": ["b", "d"], "b": ["c"], "c": ["d"], "d": ["e"], "e": []})
    assert graph.shortest_path_lengths("a") == {"a": 0, "b": 1, "d": 1, "c": 2, "e": 2}


def test_breadth_first_order_with_cycle():
    graph = Graph({1: [2, 3], 2: [4, 1], 3: [4], 4: [5, 1], 5: []})
    result = graph.shortest_path_lengths(1)
    assert list(result.items()) == [(1, 0), (2, 1), (3, 1), (4, 2), (5, 3)]


@pytest.mark.nz_workload
def test_workload_grid():
    result = grid(30, 30).shortest_path_lengths(0)
    assert len(result) == 900
    assert list(result)[:3] == [0, 1, 30]
    assert result[899] == 58
    assert result[31] == 2
