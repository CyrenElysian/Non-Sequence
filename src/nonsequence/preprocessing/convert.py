"""Convert simplified ProScript data to the project graph schema."""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from nonsequence.common import atomic_write_json, load_json

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTERIM_DIR = PROJECT_ROOT / "data" / "interim" / "ctrlscript"
DEFAULT_INPUT = INTERIM_DIR / "proscript_simple_dev.json"
DEFAULT_OUTPUT = INTERIM_DIR / "converted_dev.json"


def build_graph(
    edges: list[str], num_nodes: int
) -> tuple[dict[int, list[int]], list[int]]:
    """Build adjacency lists and node in-degrees."""
    adj: dict[int, list[int]] = defaultdict(list)
    in_degree = [0] * num_nodes
    for edge in edges:
        u, v = map(int, edge.split("->"))
        adj[u].append(v)
        in_degree[v] += 1
    return adj, in_degree

def topological_sort(
    adj: dict[int, list[int]], in_degree: list[int], num_nodes: int
) -> list[int]:
    """Return a topological order of the graph."""
    indeg = in_degree[:]
    q = deque([i for i in range(num_nodes) if indeg[i] == 0])
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    return order

def find_and_join_structure(
    adj: dict[int, list[int]], in_degree: list[int], num_nodes: int
) -> dict[int, tuple[int, list[list[int]]]]:
    """Identify fork/join regions whose branches are disjoint linear chains."""
    forks = [i for i in range(num_nodes) if len(adj[i]) >= 2]
    joins = [i for i in range(num_nodes) if in_degree[i] >= 2]
    and_joins: dict[int, tuple[int, list[list[int]]]] = {}

    for s in forks:
        for t in joins:
            if s == t:
                continue
            branches: list[list[int]] = []
            valid = True
            visited_global = set()
            for start in adj[s]:
                branch = []
                cur = start
                while cur != t:
                    if cur in visited_global:
                        valid = False
                        break
                    visited_global.add(cur)
                    branch.append(cur)
                    if len(adj[cur]) != 1:
                        valid = False
                        break
                    cur = adj[cur][0]
                if not valid:
                    break
                branches.append(branch)
            if not valid or len(branches) < 2:
                continue

            t_incoming: set[int] = set()
            for u in range(num_nodes):
                if t in adj[u]:
                    t_incoming.add(u)
            expected_incoming: set[int] = set()
            for branch in branches:
                if branch:
                    expected_incoming.add(branch[-1])
                else:
                    expected_incoming.add(s)
            if t_incoming == expected_incoming:
                and_joins[t] = (s, branches)
    return and_joins

def build_script_graph(
    adj: dict[int, list[int]],
    in_degree: list[int],
    num_nodes: int,
    and_joins: dict[int, tuple[int, list[list[int]]]],
) -> dict[str, Any]:
    """Build the nested script graph from graph structure."""
    topo = topological_sort(adj, in_degree, num_nodes)
    covered = [False] * num_nodes
    script = []

    i = 0
    while i < len(topo):
        node = topo[i]
        if covered[node]:
            i += 1
            continue

        fork_of = None
        for t, (s, branches) in and_joins.items():
            if s == node:
                fork_of = (s, t, branches)
                break

        if fork_of:
            s, t, branches = fork_of
            script.append(str(s))
            covered[s] = True

            branches_dict: dict[str, list[str]] = {}
            for idx, branch in enumerate(branches, start=1):
                branches_dict[f"b{idx}"] = [str(n) for n in branch]
                for n in branch:
                    covered[n] = True
            script.append({
                "type": "and_join",
                "branches_set": branches_dict
            })
            i += 1
            continue

        if not covered[node]:
            script.append(str(node))
            covered[node] = True
        i += 1

    return {
        "type": "sequence",
        "script": script
    }

def convert_scenario(item: dict[str, Any], idx: int) -> dict[str, Any]:
    """Convert one source scenario without changing graph semantics."""
    scenario = item["scenario"]
    unordered_nodes = item["events"]
    num_nodes = len(unordered_nodes)
    edges = item["gold_edges_for_prediction"]

    adj, in_degree = build_graph(edges, num_nodes)
    and_joins = find_and_join_structure(adj, in_degree, num_nodes)
    script_graph = build_script_graph(adj, in_degree, num_nodes, and_joins)

    new_item = {
        "id": idx + 1,
        "scenario": scenario,
        "unordered_nodes": unordered_nodes,
        "edges": edges,
        "script_graph": script_graph
    }
    return new_item

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    """Convert a dataset file."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        data = load_json(args.input)
    except (OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1
    if not isinstance(data, list):
        LOGGER.error("Input JSON must be an array")
        return 1

    converted: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        try:
            converted.append(convert_scenario(item, i))
        except (KeyError, TypeError, ValueError, IndexError) as error:
            scenario = item.get("scenario", "unknown") if isinstance(item, dict) else "unknown"
            LOGGER.error("Could not convert item %d (%s): %s", i, scenario, error)

    try:
        atomic_write_json(args.output, converted)
    except OSError as error:
        LOGGER.error("%s", error)
        return 1
    LOGGER.info("Wrote %d converted items to %s", len(converted), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())