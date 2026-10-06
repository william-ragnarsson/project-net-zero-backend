def shortest_path_lengths(self, source: Hashable) -> dict[Hashable, int]:
    """Return the number of edges on a shortest path from ``source`` to each node.

    Only nodes reachable from ``source`` appear in the result, ``source``
    itself with distance 0. Nodes are listed in breadth-first order.
    """
    distances = {source: 0}
    visited = [source]
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for neighbour in self.adjacency.get(node, []):
            if neighbour not in visited:
                visited.append(neighbour)
                distances[neighbour] = distances[node] + 1
                queue.append(neighbour)
    return distances
