"""A minimal unweighted graph."""

from collections.abc import Hashable
from dataclasses import dataclass, field


@dataclass
class Graph:
    """A directed, unweighted graph stored as ``node -> list of neighbours``.

    Add an edge in both directions for an undirected graph. Nodes may be any
    hashable value.
    """

    adjacency: dict[Hashable, list[Hashable]] = field(default_factory=dict)

    def shortest_path_lengths(self, source: Hashable) -> dict[Hashable, int]:
        """Return the number of edges on a shortest path from ``source`` to each node.

        Only nodes reachable from ``source`` appear in the result, ``source``
        itself with distance 0. Nodes are listed in breadth-first order.
        """
        distances = {source: 0}
        visited = [source]
        queue = [source]
        while queue:
            node = queue.pop(0)
            for neighbour in self.adjacency.get(node, []):
                if neighbour not in visited:
                    visited.append(neighbour)
                    distances[neighbour] = distances[node] + 1
                    queue.append(neighbour)
        return distances
