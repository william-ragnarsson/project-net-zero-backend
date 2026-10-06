def shortest_path_lengths(self, source: Hashable) -> dict[Hashable, int]:
    """Return the number of edges on a shortest path from ``source`` to each node.

    Only nodes reachable from ``source`` appear in the result, ``source``
    itself with distance 0. Nodes are listed in breadth-first order.
    """
    distances = {source: 0}
    adjacency = self.adjacency
    frontier = [source]
    depth = 0
    while frontier:
        depth += 1
        next_frontier = []
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in distances:
                    distances[neighbour] = depth
                    next_frontier.append(neighbour)
        frontier = next_frontier
    return distances
