"""Report graph statistics grouped by script-structure category."""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Final

from nonsequence.common import load_json

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT: Final = (
    PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript_with_stats.json"
)
CATEGORIES = ("linear", "only_select", "only_loop", "only_and_join", "mixed")


def parse_edges(edges: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Return out-degree and in-degree maps."""
    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    for edge in edges:
        source, target = edge.split("->")
        out_degree[source] = out_degree.get(source, 0) + 1
        in_degree[target] = in_degree.get(target, 0) + 1
    return out_degree, in_degree


def classify(type_count: dict[str, int]) -> str:
    """Classify an item by its non-sequence structure types."""
    select = type_count.get("select", 0)
    loop = type_count.get("loop", 0)
    and_join = type_count.get("and_join", 0)
    if select == loop == and_join == 0:
        return "linear"
    if sum(value > 0 for value in (select, loop, and_join)) > 1:
        return "mixed"
    if select > 0:
        return "only_select"
    if loop > 0:
        return "only_loop"
    return "only_and_join"


def compute_stats(items: list[dict[str, float]]) -> dict[str, float]:
    """Compute aggregate node, edge, and degree statistics."""
    count = len(items)
    if count == 0:
        return {"count": 0, "avg_nodes": 0, "avg_edges": 0, "avg_deg": 0}
    return {
        "count": count,
        "avg_nodes": sum(item["nodes"] for item in items) / count,
        "avg_edges": sum(item["edges"] for item in items) / count,
        "avg_deg": sum(item["avg_deg"] for item in items) / count,
    }


def log_stats(title: str, items: list[dict[str, float]]) -> None:
    """Log aggregate statistics for a group."""
    stats = compute_stats(items)
    LOGGER.info(
        "%s: count=%d, avg_nodes=%.2f, avg_edges=%.2f, avg_degree=%.2f",
        title,
        stats["count"],
        stats["avg_nodes"],
        stats["avg_edges"],
        stats["avg_deg"],
    )


def report(data: list[dict[str, Any]]) -> None:
    """Calculate and log all category and maximum-degree statistics."""
    categories: dict[str, list[dict[str, float]]] = {
        name: [] for name in CATEGORIES
    }
    overall: list[dict[str, float]] = []
    nonlinear: list[dict[str, float]] = []
    max_degrees: Counter[int] = Counter()
    nonlinear_max_degrees: Counter[int] = Counter()
    high_degree_count = 0
    nonlinear_high_degree_count = 0

    for item in data:
        nodes = len(item["unordered_nodes"])
        edge_count = len(item["edges"])
        out_degree, in_degree = parse_edges(item["edges"])
        average_degree = edge_count / nodes if nodes else 0.0
        all_nodes = set(out_degree) | set(in_degree)
        max_degree = max(
            (out_degree.get(node, 0) + in_degree.get(node, 0) for node in all_nodes),
            default=0,
        )
        has_high_degree = any(value > 1 for value in out_degree.values()) or any(
            value > 1 for value in in_degree.values()
        )
        metrics = {
            "nodes": float(nodes),
            "edges": float(edge_count),
            "avg_deg": average_degree,
        }
        category = classify(item["type_cnt"])
        categories[category].append(metrics)
        overall.append(metrics)
        max_degrees[max_degree] += 1
        high_degree_count += int(has_high_degree)
        if category != "linear":
            nonlinear.append(metrics)
            nonlinear_max_degrees[max_degree] += 1
            nonlinear_high_degree_count += int(has_high_degree)

    for category in CATEGORIES:
        log_stats(category, categories[category])
    log_stats("overall", overall)
    LOGGER.info("Overall graphs with in-degree or out-degree above one: %d", high_degree_count)
    LOGGER.info("Overall maximum-degree distribution: %s", dict(sorted(max_degrees.items())))
    log_stats("nonlinear", nonlinear)
    LOGGER.info(
        "Nonlinear graphs with in-degree or out-degree above one: %d",
        nonlinear_high_degree_count,
    )
    LOGGER.info(
        "Nonlinear maximum-degree distribution: %s",
        dict(sorted(nonlinear_max_degrees.items())),
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    return parser.parse_args()


def main() -> int:
    """Load a dataset and report statistics."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        data = load_json(args.input)
        if not isinstance(data, list):
            raise ValueError("Input JSON must be an array")
        report(data)
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
