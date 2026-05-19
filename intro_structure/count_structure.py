"""统计 script_graph 的最大嵌套深度与各 type 数量，并写回数据。

用法示例：
    python count_structure.py -i processed_data.json -o processed_data_with_stats.json

要求：
    - 不会覆盖原文件（输入输出路径必须不同；若需强制可加 --force）。
    - 每条数据会新增两个字段：
        * "max_depth": int，结构性节点的最大嵌套层级（最外层 type 节点算 1）。
        * "type_cnt" : dict，固定键顺序 sequence / select / loop / and_join / total。
    - 仅统计结构节点的 type；节点 id（"3"）以及 loop.retry 里的 "continue"
      占位符不计入。

script_graph 各 type 的子节点字段约定：
    sequence : script        (list)
    select   : options       (list)
    and_join : branches_set  (dict: branch_name -> list)
    loop     : entry (单节点) / retry (list, 含 "continue" 占位) / exit (单节点)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

STRUCT_TYPES: List[str] = ["sequence", "select", "loop", "and_join"]


def _iter_children(node: Dict[str, Any]) -> List[Any]:
    """根据 type 提取该结构节点下的所有直接子节点（已展平为一个列表）。"""
    children: List[Any] = []
    t = node.get("type")

    if t == "sequence":
        children.extend(node.get("script", []) or [])
    elif t == "select":
        children.extend(node.get("options", []) or [])
    elif t == "and_join":
        branches_set = node.get("branches_set", {}) or {}
        if isinstance(branches_set, dict):
            for branch in branches_set.values():
                if isinstance(branch, list):
                    children.extend(branch)
    elif t == "loop":
        if "entry" in node:
            children.append(node["entry"])
        retry = node.get("retry", []) or []
        if isinstance(retry, list):
            children.extend(retry)
        if "exit" in node:
            children.append(node["exit"])
    else:
        # 未知 type：尽量兼容，遍历常见的容器字段
        for key in ("script", "options"):
            if isinstance(node.get(key), list):
                children.extend(node[key])

    return children


def analyze(node: Any, depth: int = 1) -> Tuple[int, Dict[str, int]]:
    """递归计算结构节点的最大嵌套深度与各 type 数量。

    Args:
        node: 当前节点（可能是 dict / str / "continue" / 其它叶子）。
        depth: 当前节点（若为结构节点）的层级，最外层从 1 开始。

    Returns:
        (max_depth, counts)
            max_depth: 子树中出现过的最大层级（无结构节点时为 0）。
            counts   : {type_name: count}
    """
    # 叶子：节点 id 字符串 / 数字 / loop 中的 "continue" 占位符 / None
    if not isinstance(node, dict):
        return 0, {}

    t = node.get("type")
    counts: Dict[str, int] = {}
    max_depth = 0

    if t in STRUCT_TYPES:
        counts[t] = 1
        max_depth = depth
        child_depth = depth + 1
    else:
        # 没有 type 字段的 dict（一般不会出现），不计深度
        child_depth = depth

    for child in _iter_children(node):
        if child == "continue":  # loop 中的占位标记
            continue
        sub_depth, sub_counts = analyze(child, child_depth)
        if sub_depth > max_depth:
            max_depth = sub_depth
        for k, v in sub_counts.items():
            counts[k] = counts.get(k, 0) + v

    return max_depth, counts


def build_type_cnt(counts: Dict[str, int]) -> Dict[str, int]:
    """按固定顺序补齐缺失类型，并追加 total 字段。"""
    result: Dict[str, int] = {}
    total = 0
    for name in STRUCT_TYPES:
        c = int(counts.get(name, 0))
        result[name] = c
        total += c
    result["total"] = total
    return result


def process_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """对单条数据计算并写入 max_depth / type_cnt。"""
    sg = item.get("script_graph")
    if sg is None:
        item["max_depth"] = 0
        item["type_cnt"] = build_type_cnt({})
        return item

    max_depth, counts = analyze(sg, depth=1)
    item["max_depth"] = max_depth
    item["type_cnt"] = build_type_cnt(counts)
    return item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计 script_graph 的最大嵌套深度与各 type 数量，并写回到新文件。",
    )
    parser.add_argument(
        "-i", "--input", required=True,
        help="输入 JSON 文件路径（数组形式，每个元素含 script_graph 字段）。",
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="输出 JSON 文件路径（不能与输入相同，除非 --force）。",
    )
    parser.add_argument(
        "--indent", type=int, default=4,
        help="输出 JSON 的缩进空格数（默认 4，传 0 表示压缩输出）。",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="允许输出路径与输入路径相同（默认禁止，避免误覆盖）。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    in_path = os.path.abspath(args.input)
    out_path = os.path.abspath(args.output)

    if not os.path.isfile(in_path):
        print(f"[错误] 输入文件不存在: {in_path}", file=sys.stderr)
        return 1

    if in_path == out_path and not args.force:
        print(
            "[错误] 输出路径与输入路径相同，已拒绝覆盖。"
            "如需强制覆盖请加 --force。",
            file=sys.stderr,
        )
        return 1

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("[错误] 输入 JSON 顶层必须是数组（list）。", file=sys.stderr)
        return 1

    for item in data:
        if isinstance(item, dict):
            process_item(item)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    indent = args.indent if args.indent and args.indent > 0 else None
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)

    print(f"[完成] 已处理 {len(data)} 条数据 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
