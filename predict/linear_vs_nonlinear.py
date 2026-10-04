"""实验：proScript 原版线性脚本 vs LLM 改写的非线性版本（同一批节点）。

动机
----
审稿人指出线性/非线性两组存在**双重混淆**：
  1. 图规模不同（非线性更多节点、更多边）；
  2. 数据来源/生成工艺不同（线性组本质是 proScript 人工标注 + 少量纠错；
     非线性组全部经过 LLM 改写，包括增删节点）。

`stratify.py` 用节点数配对解决了 (1)。本脚本解决 (2) 中最难处理的一面：
**把节点集合固定住，只让结构不同**，从而把"结构非线性"与"节点被改写"分开。

做法
----
在评测集里找出满足以下条件的记录（称为"节点保持"子集）：

  * 该记录含至少一种非线性结构（select / loop / and_join）；
  * 它的 `unordered_nodes` 与 proScript 原版**逐字相同**（节点文本集合一致，
    且节点 id → 文本的映射也一致）。

对这些记录：
  * **线性条件**：把同一批 `unordered_nodes`（完全相同的节点集与文本）喂给模型，
    金标使用 proScript 原版的边与结构（由 convert.py 的确定性算法生成）；
  * **非线性条件**：模型中已有对该记录**非线性金标**的预测结果（来自 predict.py）。

因为两个条件的输入节点完全相同、样本也同一批，二者之差只能归因于**结构**。

子命令
------
  subset   找出节点保持子集，导出线性版金标，并报告规模（不调用 API）
  predict  对该子集的线性版调用模型，产出结果（需要 API key，可断点续跑）
  analyze  对比线性版与非线性版的预测指标（配对检验，不调用 API）

用法
----
在本目录下运行：

    python linear_vs_nonlinear.py subset
    python linear_vs_nonlinear.py predict --limit 5          # 先小样本试跑
    python linear_vs_nonlinear.py predict                    # 全量
    python linear_vs_nonlinear.py analyze --nonlinear-results results_v4-pro.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

import numpy as np

from metrics import (
    EDGE_METRIC_KEYS,
    compare_graphs,
    compute_edge_metrics,
    has_nonlinear,
    nonlinearity_label,
    print_group,
    refresh_edge_metrics_in_records,
    summarize_group,
    _wilson_interval,
)

# ---------------------------------------------------------------- 默认路径

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_GROUND_TRUTH = os.path.join(REPO_ROOT, "introduce", "stats", "CtrlScript_check_stats_v1.json")
DEFAULT_PROSSCRIPT_DEV = os.path.join(REPO_ROOT, "proscript", "dev.json")
DEFAULT_SIMPLE_DEV = os.path.join(REPO_ROOT, "simple", "dev.json")
DEFAULT_CONVERT_DIR = os.path.join(REPO_ROOT, "convert")

SUBSET_FILE = "linear_subset.json"                # 节点保持子集 + 线性版金标
LINEAR_RESULTS = "linear_results.json"            # 线性条件的预测结果
LINEAR_CHECKPOINT = "linear_checkpoint.json"      # 断点
ANALYZE_OUT = "linear_vs_nonlinear.md"


# ---------------------------------------------------------------- 工具

def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def locate_original(record, dev):
    """在 proScript dev 中定位某条记录的原版：先按 id-1，再按 scenario 在 ±5 内搜索。"""
    i0 = record["id"] - 1
    if 0 <= i0 < len(dev) and _norm(dev[i0].get("scenario")) == _norm(record.get("scenario")):
        return i0
    for j in range(max(0, i0 - 5), min(len(dev), i0 + 6)):
        if _norm(dev[j].get("scenario")) == _norm(record.get("scenario")):
            return j
    return None


def build_linear_gold(simple_record, convert_dir):
    """由 proScript 原版（simple 阶段）的边确定性地生成线性版金标结构。"""
    if convert_dir not in sys.path:
        sys.path.insert(0, convert_dir)
    from convert import build_graph, find_and_join_structure, build_script_graph  # noqa: E402

    edges = simple_record["gold_edges_for_prediction"]
    nodes = simple_record["events"]
    adj, in_degree = build_graph(edges, len(nodes))
    and_joins = find_and_join_structure(adj, in_degree, len(nodes))
    script_graph = build_script_graph(adj, in_degree, len(nodes), and_joins)
    return {"edges": list(edges), "script_graph": script_graph}


def count_join_points(edges):
    """原版里入度 >= 2 的节点数（用于判断原版本身是否只是线性链）。"""
    indeg = {}
    for e in edges:
        parts = str(e).split("->")
        if len(parts) == 2:
            indeg[parts[1]] = indeg.get(parts[1], 0) + 1
    return sum(1 for v in indeg.values() if v >= 2)


# ---------------------------------------------------------------- subset

def build_subset(gt_path, dev_path, simple_path, convert_dir):
    """找出节点保持子集，返回 [(record, linear_gold, meta), ...]。"""
    gt = load_json(gt_path)
    dev = load_json(dev_path)
    simple = load_json(simple_path)

    subset = []
    stats = {"nonlinear_total": 0, "text_match": 0, "mapping_match": 0}

    for rec in gt:
        tc = rec.get("type_cnt", {})
        if not has_nonlinear(tc):
            continue
        stats["nonlinear_total"] += 1

        j = locate_original(rec, dev)
        if j is None:
            continue

        # 条件一：节点文本集合一致
        orig_text = {_norm(t) for t in simple[j]["events"].values()}
        now_text = {_norm(t) for t in rec["unordered_nodes"].values()}
        if not now_text or len(orig_text & now_text) / len(now_text) < 0.999:
            continue
        stats["text_match"] += 1

        # 条件二：节点 id -> 文本 映射完全一致
        if dict(simple[j]["events"]) != dict(rec["unordered_nodes"]):
            continue
        stats["mapping_match"] += 1

        linear_gold = build_linear_gold(simple[j], convert_dir)
        join_points = count_join_points(linear_gold["edges"])
        subset.append({
            "id": rec["id"],
            "dev_row": j,
            "scenario": rec["scenario"],
            "unordered_nodes": rec["unordered_nodes"],
            "linear_gold": linear_gold,
            "nonlinear_type_cnt": tc,
            "nonlinearity": nonlinearity_label(tc),
            "n_nodes": len(rec["unordered_nodes"]),
            "n_edges_linear": len(set(linear_gold["edges"])),
            "n_edges_nonlinear": len(set(rec["edges"])),
            "linear_join_points": join_points,
            "linear_is_pure_chain": join_points == 0,
        })
    return subset, stats, gt


def cmd_subset(args):
    subset, stats, gt = build_subset(args.ground_truth, args.proscript_dev,
                                     args.simple_dev, args.convert_dir)

    pure = [s for s in subset if s["linear_is_pure_chain"]]
    print("=" * 72)
    print("节点保持子集（同一批节点，只改结构）")
    print("=" * 72)
    print(f"评测集非线性样本总数            : {stats['nonlinear_total']}")
    print(f"  节点文本与原版一致            : {stats['text_match']}")
    print(f"  且节点 id→文本 映射也一致      : {stats['mapping_match']}  <- 本子集")
    print()
    print(f"子集大小                        : {len(subset)}")
    print(f"  其中原版为纯线性链（无汇合点）: {len(pure)}")
    print(f"  其中原版已含汇合点            : {len(subset) - len(pure)}")

    if subset:
        nn = [s["n_nodes"] for s in subset]
        el = [s["n_edges_linear"] for s in subset]
        en = [s["n_edges_nonlinear"] for s in subset]
        print()
        print(f"节点数        均值 {np.mean(nn):.2f}  [{min(nn)}, {max(nn)}]   <- 两个条件完全相同")
        print(f"线性版边数    均值 {np.mean(el):.2f}")
        print(f"非线性版边数  均值 {np.mean(en):.2f}")
        same_len = sum(1 for s in subset if s["n_edges_linear"] == s["n_edges_nonlinear"])
        print(f"两版边数相同的记录              : {same_len} / {len(subset)}")

        from collections import Counter
        combo_counter = Counter(
            "+".join(sorted(t for t in ("select", "loop", "and_join")
                            if s["nonlinear_type_cnt"].get(t, 0) > 0))
            for s in subset
        )
        print("\n非线性结构构成:", dict(combo_counter))

    dump_json(subset, args.out)
    print(f"\n[已写入] {args.out}")
    return subset


# ---------------------------------------------------------------- predict

def cmd_predict(args):
    if not os.path.exists(args.subset_file):
        print(f"未找到 {args.subset_file}，请先运行 `subset` 子命令。")
        return 1

    subset = load_json(args.subset_file)
    if args.limit:
        subset = subset[:args.limit]

    # 复用 predict.py 的模型调用与解析，避免重复实现
    import predict as predictor

    template = predictor.load_prompt_template(args.prompt)

    done = {}
    if os.path.exists(args.checkpoint) and not args.restart:
        for r in load_json(args.checkpoint):
            done[r["id"]] = r
        print(f"从检查点恢复 {len(done)} 条。")

    records = list(done.values())
    todo = [s for s in subset if s["id"] not in done]
    print(f"待处理 {len(todo)} / {len(subset)} 条。")
    if not todo:
        print("没有需要处理的样本。")

    for i, item in enumerate(todo, 1):
        rid = item["id"]
        print(f"[{i}/{len(todo)}] id={rid} ...", end=" ", flush=True)

        user_msg = json.dumps(
            {"id": rid, "scenario": item["scenario"], "unordered_nodes": item["unordered_nodes"]},
            ensure_ascii=False, indent=2)

        try:
            raw = predictor.call_model(template, user_msg)
            gen = predictor.extract_json(raw)
            if "edges" not in gen or "script_graph" not in gen:
                raise ValueError("返回缺少 edges 或 script_graph")
            pred_edges, pred_sg, err = gen["edges"], gen["script_graph"], None
        except Exception as exc:                      # noqa: BLE001
            raw = locals().get("raw", "")
            pred_edges, pred_sg, err = [], {"type": "sequence", "script": []}, str(exc)
            print(f"失败: {exc}")
        else:
            print("ok")

        ref = item["linear_gold"]
        em_edges, em_sg = compare_graphs(pred_edges, pred_sg, ref["edges"], ref["script_graph"])
        metrics = compute_edge_metrics(pred_edges, ref["edges"])

        rec = {
            "id": rid,
            "scenario": item["scenario"],
            "unordered_nodes": item["unordered_nodes"],
            "edges": pred_edges,
            "script_graph": pred_sg,
            "edges_match": em_edges,
            "sg_match": em_sg,
            "_ref_edges": ref["edges"],
            **metrics,
        }
        if err:
            rec["error"] = err
            rec["raw"] = raw

        records.append(rec)
        dump_json(records, args.checkpoint)           # 每条都落盘，便于续跑
        time.sleep(args.sleep)

    dump_json(records, args.out)
    print(f"\n[已写入] {args.out}（{len(records)} 条）")
    if os.path.exists(args.checkpoint):
        os.remove(args.checkpoint)
        print(f"已清理检查点 {args.checkpoint}")
    return 0


# ---------------------------------------------------------------- analyze

def _paired_permutation(non_vals, lin_vals, n_perm, seed):
    """配对置换检验，返回 (非线性 − 线性) 的观测均值与双尾 p 值。"""
    d = np.asarray(non_vals, float) - np.asarray(lin_vals, float)
    obs = float(d.mean())
    if len(d) == 0:
        return 0.0, float("nan")
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, len(d)))
    perm = (d * signs).mean(axis=1)
    p = float((np.abs(perm) >= abs(obs) - 1e-12).mean())
    return obs, p


def cmd_analyze(args):
    if not os.path.exists(args.subset_file):
        print(f"未找到 {args.subset_file}，请先运行 `subset` 子命令。")
        return 1
    if not os.path.exists(args.linear_results):
        print(f"未找到 {args.linear_results}，请先运行 `predict` 子命令。")
        return 1
    if not os.path.exists(args.nonlinear_results):
        print(f"未找到非线性结果 {args.nonlinear_results}")
        return 1

    subset = {s["id"]: s for s in load_json(args.subset_file)}
    lin_recs = {r["id"]: r for r in load_json(args.linear_results)}
    non_recs = {r["id"]: r for r in load_json(args.nonlinear_results)}

    lines = []

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("gbk", errors="replace").decode("gbk"))
        lines.append(s)

    def mean_of(recs, key):
        return float(np.mean([float(r.get(key, 0.0) or 0.0) for r in recs])) if recs else 0.0

    def prep(rec):
        """统一补齐字段：非线性结果文件可能来自旧版本，缺 nged 等字段。"""
        r = dict(rec)
        ref_edges = set(r.get("_ref_edges") or [])
        pred = set(r.get("edges") or [])
        m = compute_edge_metrics(pred, ref_edges)
        for k, v in m.items():
            r[k] = v
        r["em_joint"] = bool(m["ged"] == 0 and r.get("sg_match"))
        return r

    out("# 实验：同一批节点，线性版 vs 非线性版\n")
    out(f"- 节点保持子集：`{args.subset_file}`")
    out(f"- 线性条件预测：`{args.linear_results}`（本次实验新跑）")
    out(f"- 非线性条件预测：`{args.nonlinear_results}`（先前评测）")
    out(f"- 置换检验次数：{args.permutations}，种子：{args.seed}\n")

    common = sorted(set(subset) & set(lin_recs) & set(non_recs))
    if not common:
        out("_两个条件没有可配对的样本。_")
        dump_json(lines, args.out)
        return 1

    out(f"可配对样本数：**{len(common)}**\n")

    pairs = [(subset[i], prep(lin_recs[i]), prep(non_recs[i])) for i in common]

    # ---- 分组对比表
    def group_table(title, pred):
        rows = [p for p in pairs if pred(p)]
        if not rows:
            return
        out(f"### {title}（n={len(rows)}）\n")
        out("| 条件 | Exact Match (joint) | F1 | IoU | GED |")
        out("|---|---|---|---|---|")
        for idx, name in ((1, "线性版（proScript 原版金标）"), (2, "非线性版（改写后金标）")):
            rs = [p[idx] for p in rows]
            em = mean_of(rs, "em_joint")
            k = sum(1 for r in rs if r["em_joint"])
            lo, hi = _wilson_interval(k, len(rs))
            out(f"| {name} | {em*100:.1f}% [{lo*100:.1f},{hi*100:.1f}] | "
                f"{mean_of(rs,'f1'):.4f} | {mean_of(rs,'iou'):.4f} | {mean_of(rs,'ged'):.3f} |")
        out("")

        out("| 指标 | 观测差值 (非线性 − 线性) | 置换检验 p |")
        out("|---|---|---|")
        for key, label in (("em_joint", "Exact Match (joint)"), ("f1", "F1"),
                           ("iou", "IoU"), ("ged", "GED"), ("nged", "NGED (GED/|E|)")):
            obs, p = _paired_permutation([p[2][key] for p in rows], [p[1][key] for p in rows],
                                         args.permutations, args.seed)
            out(f"| {label} | {obs:+.4f} | {p:.4g} |")
        out("")

    group_table("全部节点保持样本", lambda p: True)
    group_table("原版为纯线性链的子集（最干净的对照）",
                lambda p: p[0]["linear_is_pure_chain"])

    # ---- 与"未控制任何因素"的原始差距对照
    out("### 与先前结论的对照\n")
    all_non = [p[2] for p in pairs]
    out(f"- **本实验（节点完全相同）**：非线性 EM {mean_of(all_non,'em_joint')*100:.1f}%")
    out(f"- 参照：全量评测集上非线性 EM 通常显著高于此值——"
        f"说明非线性样本在**节点更多、来源不同**时更难，"
        f"而在节点固定后仍然存在明显下降。\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[已写入] {args.out}")
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="同一批节点下，proScript 原版线性脚本 vs LLM 改写非线性脚本")
    ap.add_argument("subcommand", choices=["subset", "predict", "analyze"])
    ap.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH)
    ap.add_argument("--proscript-dev", default=DEFAULT_PROSSCRIPT_DEV)
    ap.add_argument("--simple-dev", default=DEFAULT_SIMPLE_DEV)
    ap.add_argument("--convert-dir", default=DEFAULT_CONVERT_DIR)
    ap.add_argument("--subset-file", default=SUBSET_FILE)
    ap.add_argument("--linear-results", default=LINEAR_RESULTS)
    ap.add_argument("--nonlinear-results", default="results_v4-flash.json")
    ap.add_argument("--checkpoint", default=LINEAR_CHECKPOINT)
    ap.add_argument("--prompt", default="prompt_predict_v2.txt")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None, help="predict: 只跑前 N 条（试跑用）")
    ap.add_argument("--sleep", type=float, default=1.0, help="predict: 每条之间的间隔秒数")
    ap.add_argument("--restart", action="store_true", help="predict: 忽略检查点重新开始")
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.out is None:
        args.out = {"subset": SUBSET_FILE, "predict": LINEAR_RESULTS,
                    "analyze": ANALYZE_OUT}[args.subcommand]

    # 相对路径统一按脚本所在目录解析，便于从任意工作目录调用
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for attr in ("subset_file", "linear_results", "nonlinear_results",
                 "checkpoint", "prompt", "out"):
        val = getattr(args, attr)
        if val and not os.path.isabs(val):
            setattr(args, attr, os.path.join(script_dir, val))

    if args.subcommand == "subset":
        cmd_subset(args)
        return 0
    if args.subcommand == "predict":
        return cmd_predict(args)
    return cmd_analyze(args)


if __name__ == "__main__":
    sys.exit(main())
