"""Add script-graph nesting depth and structure-type counts to a dataset."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from nonsequence.common import atomic_write_json, load_json

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ctrlscript" / "CtrlScript_with_stats.json"
STRUCT_TYPES = ("sequence", "select", "loop", "and_join")


def iter_children(node: dict[str, Any]) -> list[Any]:
    """Return direct children of a structural node as a flat list."""
    children: list[Any] = []
    node_type = node.get("type")
    if node_type == "sequence":
        children.extend(node.get("script", []) or [])
    elif node_type == "select":
        children.extend(node.get("options", []) or [])
    elif node_type == "and_join":
        branches = node.get("branches_set", {}) or {}
        if isinstance(branches, dict):
            for branch in branches.values():
                if isinstance(branch, list):
                    children.extend(branch)
    elif node_type == "loop":
        if "entry" in node:
            children.append(node["entry"])
        retry = node.get("retry", []) or []
        if isinstance(retry, list):
            children.extend(retry)
        if "exit" in node:
            children.append(node["exit"])
    else:
        for key in ("script", "options"):
            value = node.get(key)
            if isinstance(value, list):
                children.extend(value)
    return children


def analyze(node: Any, depth: int = 1) -> tuple[int, dict[str, int]]:
    """Recursively calculate maximum structural depth and type counts."""
    if not isinstance(node, dict):
        return 0, {}
    node_type = node.get("type")
    counts: dict[str, int] = {}
    max_depth = 0
    if node_type in STRUCT_TYPES:
        counts[node_type] = 1
        max_depth = depth
        child_depth = depth + 1
    else:
        child_depth = depth
    for child in iter_children(node):
        if child == "continue":
            continue
        sub_depth, sub_counts = analyze(child, child_depth)
        max_depth = max(max_depth, sub_depth)
        for name, count in sub_counts.items():
            counts[name] = counts.get(name, 0) + count
    return max_depth, counts


def build_type_count(counts: dict[str, int]) -> dict[str, int]:
    """Fill all known structure types and append their total."""
    result = {name: int(counts.get(name, 0)) for name in STRUCT_TYPES}
    result["total"] = sum(result.values())
    return result


def process_item(item: dict[str, Any]) -> dict[str, Any]:
    """Add statistics to one item in place."""
    graph = item.get("script_graph")
    max_depth, counts = analyze(graph, depth=1) if graph is not None else (0, {})
    item["max_depth"] = max_depth
    item["type_cnt"] = build_type_count(counts)
    return item


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--indent", type=int, default=4)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow the output path to equal the input path.",
    )
    return parser.parse_args()


def main() -> int:
    """Calculate and write structure statistics."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path and not args.force:
        LOGGER.error("Input and output paths are identical; use --force to overwrite")
        return 1
    try:
        data = load_json(input_path)
        if not isinstance(data, list):
            raise ValueError("Input JSON must be an array")
        for item in data:
            if isinstance(item, dict):
                process_item(item)
        indent = args.indent if args.indent > 0 else None
        atomic_write_json(output_path, data, indent=indent)
    except (OSError, ValueError, TypeError) as error:
        LOGGER.error("%s", error)
        return 1
    LOGGER.info("Processed %d items into %s", len(data), output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
