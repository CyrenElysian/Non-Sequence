"""金标数据加载。

标准数据集：`introduce/stats/CtrlScript_v3_stats.json`（1076 条，已含 type_cnt / max_depth）。

两点关键设计
------------
1. **stats 缺失时就地计算**。`CtrlScript_v3.json`（无 stats）只有 5 个字段；
   旧脚本把路径写死指向 v1 的 stats 文件，一旦改指向无 stats 的版本，
   会静默把全部记录判为 linear、深度全为 0。这里在字段缺失时就地计算
   （口径同 `introduce/stats/count_structure.py`），保证任何输入都能正确分组。

2. **stats 存在时仍逐条复算并比对**。这是针对"stats 文件与数据版本不一致"
   这类静默错误的防线：历史事故正是 v1 的 stats 被用在另一版数据上，
   结果 Table 1 与评测分组全部错位。此处**无论文件是否带 stats 都会重算一遍**，
   不一致则醒目告警并写入 manifest（不中断运行，便于数据仍在修订时迭代）。
"""

from __future__ import annotations

import json
import os

from .structures import depth_and_type_cnt

# 标准数据集（已含 type_cnt / max_depth）。
# 全项目只在此处定义一次，各实验脚本统一引用，避免路径散落多处导致版本错配。
DEFAULT_GROUND_TRUTH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "introduce", "stats", "CtrlScript_v3_stats.json")


def sha256_file(path, chunk=1 << 20) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_gold(path, verify_stats=True):
    """加载金标数据集。

    返回 (records, reference_graphs, reference_stats, meta)

      records           原始列表（保持文件顺序，含全部字段）
      reference_graphs  {id: {"edges": [...], "script_graph": {...}}}
      reference_stats   {id: {"max_depth": int, "type_cnt": {...}}}
      meta              {"file","sha256","n","stats_source",
                         "stats_computed","stats_from_file",
                         "stats_mismatch","stats_mismatch_examples"}
    """
    path = os.path.abspath(path)
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise ValueError(f"金标文件顶层必须是数组：{path}")

    reference_graphs, reference_stats = {}, {}
    computed = from_file = 0
    mismatches = []

    for item in records:
        rid = item.get("id")
        if "edges" not in item or "script_graph" not in item:
            raise ValueError(f"金标记录 id={rid} 缺少 edges 或 script_graph")

        reference_graphs[rid] = {
            "edges": item["edges"],
            "script_graph": item["script_graph"],
        }

        md_calc, tc_calc = depth_and_type_cnt(item["script_graph"])
        md_file, tc_file = item.get("max_depth"), item.get("type_cnt")

        if md_file is None or tc_file is None:
            md, tc = md_calc, tc_calc
            computed += 1
        else:
            from_file += 1
            md, tc = int(md_file), tc_file
            if verify_stats and (md != md_calc or tc != tc_calc):
                mismatches.append({
                    "id": rid,
                    "max_depth_file": md, "max_depth_calc": md_calc,
                    "type_cnt_file": tc_file, "type_cnt_calc": tc_calc,
                })

        reference_stats[rid] = {"max_depth": int(md or 0), "type_cnt": tc or {}}

    if computed and not from_file:
        source = "computed"
    elif from_file and not computed:
        source = "file"
    else:
        source = "mixed"

    meta = {
        "file": path,
        "sha256": sha256_file(path),
        "n": len(records),
        "stats_source": source,
        "stats_computed": computed,
        "stats_from_file": from_file,
        "stats_mismatch": len(mismatches),
        "stats_mismatch_examples": mismatches[:5],
    }

    if mismatches:
        print("!" * 78)
        print(f"[!] 警告：stats 字段与数据不一致，共 {len(mismatches)} 条")
        print(f"    文件：{path}")
        print("    这会导致 by_linearity / by_depth / by_purity 分组错误。")
        print("    请确认该 stats 文件确实对应当前数据版本，或改用无 stats 的数据让脚本就地计算。")
        for m in mismatches[:5]:
            print(f"    id={m['id']} max_depth 文件={m['max_depth_file']} 重算={m['max_depth_calc']} | "
                  f"type_cnt 文件={m['type_cnt_file']} 重算={m['type_cnt_calc']}")
        if len(mismatches) > 5:
            print(f"    ...（另有 {len(mismatches) - 5} 条）")
        print("!" * 78)

    return records, reference_graphs, reference_stats, meta


def load_records(path):
    """加载预测结果 / checkpoint（顶层数组）。"""
    with open(os.path.abspath(path), "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"记录文件顶层必须是数组：{path}")
    return data
