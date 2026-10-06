def shortest_path_lengths(self, source: Hashable) -> dict[Hashable, int]:
    """Return the number of edges on a shortest path from ``source`` to each node.

    Only nodes reachable from ``source`` appear in the result, ``source``
    itself with distance 0. Nodes are listed in breadth-first order.
    """
    distances = {source: 0}
    queue = deque([source])
    adjacency = self.adjacency
    while queue:
        node = queue.popleft()
        next_distance = distances[node] + 1
        for neighbour in adjacency.get(node, []):
            if neighbour not in distances:
                distances[neighbour] = next_distance
                queue.append(neighbour)
    return distances
