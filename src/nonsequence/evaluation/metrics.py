"""Pure metrics for graph-prediction evaluation."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

EDGE_METRIC_KEYS = ("precision", "recall", "f1", "iou", "ged", "e_del", "e_ins")
TYPE_NAMES = ("select", "loop", "and_join")


def normalize_graph(
    edges: Iterable[str] | None, script_graph: Any
) -> tuple[list[str], str]:
    """Return the canonical forms used by the original exact-match logic."""
    normalized_edges = sorted(set(edges or []))
    script_graph_json = json.dumps(
        script_graph, sort_keys=True, ensure_ascii=False
    )
    return normalized_edges, script_graph_json


def compare_graphs(
    predicted_edges: Iterable[str] | None,
    predicted_script_graph: Any,
    reference_edges: Iterable[str] | None,
    reference_script_graph: Any,
) -> tuple[bool, bool]:
    """Compare edge sets and script graphs with the original semantics."""
    predicted_edge_set, predicted_script = normalize_graph(
        predicted_edges, predicted_script_graph
    )
    reference_edge_set, reference_script = normalize_graph(
        reference_edges, reference_script_graph
    )
    return (
        predicted_edge_set == reference_edge_set,
        predicted_script == reference_script,
    )


def compute_edge_ged(
    predicted_edges: Iterable[str] | None,
    reference_edges: Iterable[str] | None,
) -> dict[str, int]:
    """Compute unit-cost edge deletions and insertions."""
    predicted = set(predicted_edges or [])
    reference = set(reference_edges or [])
    edge_deletions = len(predicted - reference)
    edge_insertions = len(reference - predicted)
    return {
        "ged": edge_deletions + edge_insertions,
        "e_del": edge_deletions,
        "e_ins": edge_insertions,
    }


def compute_edge_metrics(
    predicted_edges: Iterable[str] | None,
    reference_edges: Iterable[str] | None,
) -> dict[str, float | int]:
    """Compute macro-sample edge P/R/F1/IoU and edge-only GED."""
    predicted = set(predicted_edges or [])
    reference = set(reference_edges or [])
    intersection_size = len(predicted & reference)
    union_size = len(predicted | reference)

    precision = intersection_size / len(predicted) if predicted else 0.0
    recall = intersection_size / len(reference) if reference else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    iou = intersection_size / union_size if union_size else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "iou": iou,
        **compute_edge_ged(predicted, reference),
    }


def refresh_record_metrics(
    records: Sequence[MutableMapping[str, Any]],
    reference_graphs: Mapping[Any, Mapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    """Recompute all per-record metrics and exact-match fields in place."""
    refreshed: list[MutableMapping[str, Any]] = []
    for record in records:
        reference = reference_graphs.get(record.get("id"))
        if reference is None:
            continue
        metrics = compute_edge_metrics(record.get("edges", []), reference["edges"])
        edges_match, script_graph_match = compare_graphs(
            record.get("edges", []),
            record.get("script_graph"),
            reference["edges"],
            reference["script_graph"],
        )
        record.update(metrics)
        record["edges_match"] = edges_match
        record["sg_match"] = script_graph_match
        refreshed.append(record)
    return refreshed


def get_combo(type_counts: Mapping[str, int]) -> str:
    """Return the stable structure-combination label."""
    present = sorted(name for name in TYPE_NAMES if type_counts.get(name, 0) > 0)
    return "+".join(present) if present else "sequence"


def summarize_exact_match(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int | float]:
    """Summarize edge, script-graph, and joint exact matches."""
    count = len(records)
    if count == 0:
        return {
            "n": 0,
            "edges_match_rate": 0.0,
            "sg_match_rate": 0.0,
            "both_match_rate": 0.0,
        }
    edge_count = sum(bool(record.get("edges_match")) for record in records)
    script_count = sum(bool(record.get("sg_match")) for record in records)
    both_count = sum(
        bool(record.get("edges_match") and record.get("sg_match"))
        for record in records
    )
    return {
        "n": count,
        "edges_match_count": edge_count,
        "sg_match_count": script_count,
        "both_match_count": both_count,
        "edges_match_rate": edge_count / count,
        "sg_match_rate": script_count / count,
        "both_match_rate": both_count / count,
    }


def summarize_edge_metrics_mean(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int | float]:
    """Compute the original unweighted mean over sample-level metrics."""
    count = len(records)
    if count == 0:
        return {"n": 0, **{key: 0.0 for key in EDGE_METRIC_KEYS}}
    return {
        "n": count,
        **{
            key: sum(float(record.get(key, 0.0)) for record in records) / count
            for key in EDGE_METRIC_KEYS
        },
    }


def summarize_group(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int | float]]:
    """Summarize one evaluation group."""
    return {
        "exact_match": summarize_exact_match(records),
        "edge_metrics_mean": summarize_edge_metrics_mean(records),
    }


def compute_statistics(
    records: Sequence[Mapping[str, Any]],
    reference_stats: Mapping[Any, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build overall, depth, type, and structure-combination summaries."""
    if not records:
        return {}
    summary: dict[str, Any] = {"overall": summarize_group(records)}

    depth_groups: defaultdict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        depth = int(reference_stats.get(record["id"], {}).get("max_depth", 0))
        depth_groups[min(depth, 3)].append(record)
    summary["by_depth"] = {
        str(depth) if depth < 3 else "3+": summarize_group(group)
        for depth, group in sorted(depth_groups.items())
    }

    summary["by_structure_type"] = {}
    for type_name in TYPE_NAMES:
        group = [
            record
            for record in records
            if reference_stats.get(record["id"], {})
            .get("type_cnt", {})
            .get(type_name, 0)
            > 0
        ]
        if group:
            summary["by_structure_type"][type_name] = summarize_group(group)

    combo_groups: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        type_counts = reference_stats.get(record["id"], {}).get("type_cnt", {})
        combo_groups[get_combo(type_counts)].append(record)
    summary["by_combo"] = {
        combo: summarize_group(group)
        for combo, group in sorted(combo_groups.items())
    }
    return summary
