"""固定节点对照实验：同一批节点，proScript 原版线性结构 vs LLM 改写非线性结构
（取代旧 predict/linear_vs_nonlinear.py）。

动机
----
线性/非线性两组存在**双重混淆**：
  1. 图规模不同（非线性节点/边更多）——由 stratify.py 的节点数配对处理；
  2. 数据来源与生成工艺不同（线性组本质是 proScript 人工标注 + 少量纠错，
     非线性组全部经过 LLM 改写，含增删节点）——本脚本处理这一条。

做法：在评测集里找出"节点保持"子集（unordered_nodes 与 proScript 原版逐字相同，
且节点 id→文本映射一致），对这些记录：
  * 线性条件：把**同一批节点**喂给模型，金标用 proScript 原版的边与结构
    （由 convert.py 的确定性算法生成）
  * 非线性条件：已有的、针对该记录**非线性金标**的预测结果
两个条件的输入节点完全相同、样本同一批，因此差异只能归因于**结构**。

子命令
------
  subset   找出节点保持子集并导出线性版金标（不调 API）
  predict  对线性版调用模型，产出结果（可断点续跑）
  analyze  配对比较线性版与非线性版（不调 API）

用法
----
    python fixed_nodes.py subset
    python fixed_nodes.py predict --limit 5      # 先试跑
    python fixed_nodes.py predict
    python fixed_nodes.py analyze --nonlinear-results run_x.results.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cslib import dataset, llm, metrics, store, structures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))

DEFAULT_GROUND_TRUTH = dataset.DEFAULT_GROUND_TRUTH
DEFAULT_PROSSCRIPT_DEV = os.path.join(REPO, "proscript", "dev.json")
DEFAULT_SIMPLE_DEV = os.path.join(REPO, "simple", "dev.json")
DEFAULT_CONVERT_DIR = os.path.join(REPO, "convert")
DEFAULT_PROMPT = os.path.join(HERE, "prompts", "prompt_v2.txt")

SUBSET_FILE = "fixed_nodes.subset.json"
LINEAR_RESULTS = "fixed_nodes.linear.results.json"
LINEAR_CKPT = "fixed_nodes.linear.checkpoint.json"
ANALYZE_OUT = "fixed_nodes.md"


def resolve(path, base=HERE):
    return path if os.path.isabs(path) else os.path.join(base, path)


def norm_text(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


# ---------------------------------------------------------------- subset

def build_scenario_index(dev):
    """scenario(归一化) -> dev 行号列表，用于抗 id 漂移的全局回连。"""
    from collections import defaultdict
    index = defaultdict(list)
    for j, d in enumerate(dev):
        index[norm_text(d.get("scenario"))].append(j)
    return index


def locate_original(record, dev, index=None):
    """在 proScript dev 中定位原版，返回 (行号, 命中方式)。

    分三层，**只依据 scenario 文本**，绝不依据节点重合度——
    否则"节点与原版一致"这个子集判据会变成循环论证。

      id       : id-1 处 scenario 直接吻合（最常见）
      window   : id-1 附近 ±5 行内按 scenario 命中（应对少量删除造成的漂移）
      global   : 全局 scenario 索引命中（应对大规模删除/重排；重复 scenario 取首个）

    找不到时返回 (None, None)。注意：若 scenario 在改写阶段被修正过拼写，
    则无法回连——这属数据固有限制，会在 subset 报告里显式计数。
    """
    i0 = record["id"] - 1
    if 0 <= i0 < len(dev) and norm_text(dev[i0].get("scenario")) == norm_text(record.get("scenario")):
        return i0, "id"
    for j in range(max(0, i0 - 5), min(len(dev), i0 + 6)):
        if norm_text(dev[j].get("scenario")) == norm_text(record.get("scenario")):
            return j, "window"
    if index:
        hits = index.get(norm_text(record.get("scenario")), [])
        if hits:
            return hits[0], "global"
    return None, None


def build_linear_gold(simple_record, convert_dir):
    """由 proScript 原版的边确定性地生成线性版金标结构。"""
    if convert_dir not in sys.path:
        sys.path.insert(0, convert_dir)
    from convert import build_graph, find_and_join_structure, build_script_graph  # noqa: E402

    edges = simple_record["gold_edges_for_prediction"]
    nodes = simple_record["events"]
    adj, indeg = build_graph(edges, len(nodes))
    joins = find_and_join_structure(adj, indeg, len(nodes))
    return {"edges": list(edges), "script_graph": build_script_graph(adj, indeg, len(nodes), joins)}


def count_join_points(edges):
    indeg = {}
    for e in edges:
        parts = str(e).split("->")
        if len(parts) == 2:
            indeg[parts[1]] = indeg.get(parts[1], 0) + 1
    return sum(1 for v in indeg.values() if v >= 2)


def build_subset(gt_path, dev_path, simple_path, convert_dir):
    gold_records, _, reference_stats, _ = dataset.load_gold(gt_path)
    dev = store.load_json_safe(dev_path) or []
    simple = store.load_json_safe(simple_path) or []
    index = build_scenario_index(dev)

    subset = []
    stats = {"nonlinear_total": 0, "located": 0, "unmappable": 0,
             "text_match": 0, "mapping_match": 0, "how": {}}
    for rec in gold_records:
        tc = reference_stats[rec["id"]]["type_cnt"]
        if not structures.has_nonlinear(tc):
            continue
        stats["nonlinear_total"] += 1
        j, how = locate_original(rec, dev, index)
        if j is None:
            stats["unmappable"] += 1
            continue
        stats["located"] += 1
        stats["how"][how] = stats["how"].get(how, 0) + 1
        orig_text = {norm_text(v) for v in simple[j]["events"].values()}
        now_text = {norm_text(v) for v in rec["unordered_nodes"].values()}
        if not now_text or len(orig_text & now_text) / len(now_text) < 0.999:
            continue
        stats["text_match"] += 1
        if dict(simple[j]["events"]) != dict(rec["unordered_nodes"]):
            continue
        stats["mapping_match"] += 1
        linear_gold = build_linear_gold(simple[j], convert_dir)
        joins = count_join_points(linear_gold["edges"])
        subset.append({
            "id": rec["id"],
            "dev_row": j,
            "scenario": rec["scenario"],
            "unordered_nodes": rec["unordered_nodes"],
            "linear_gold": linear_gold,
            "nonlinear_type_cnt": tc,
            "n_nodes": len(rec["unordered_nodes"]),
            "n_edges_linear": len(set(linear_gold["edges"])),
            "n_edges_nonlinear": len(set(rec["edges"])),
            "linear_join_points": joins,
            "linear_is_pure_chain": joins == 0,
        })
    return subset, stats


def cmd_subset(args):
    subset, stats = build_subset(args.ground_truth, args.proscript_dev,
                                 args.simple_dev, args.convert_dir)
    pure = [s for s in subset if s["linear_is_pure_chain"]]
    print("=" * 76)
    print("节点保持子集（同一批节点，只改结构）")
    print("=" * 76)
    print(f"评测集非线性样本总数            : {stats['nonlinear_total']}")
    print(f"  成功回连 proScript 原版       : {stats['located']}  命中方式 {stats['how']}")
    print(f"  无法回连（scenario 被改写过）  : {stats['unmappable']}")
    print(f"  节点文本与原版一致            : {stats['text_match']}")
    print(f"  且节点 id→文本 映射也一致      : {stats['mapping_match']}  <- 本子集")
    if stats["unmappable"]:
        print(f"\n  注：无法回连的 {stats['unmappable']} 条是因 scenario 文本在改写阶段"
              f"被修正过拼写，属数据固有限制；它们不计入子集。")
    print(f"\n子集大小                        : {len(subset)}")
    print(f"  其中原版为纯线性链（无汇合点）: {len(pure)}")
    print(f"  其中原版已含汇合点            : {len(subset) - len(pure)}")
    if subset:
        nn = [s["n_nodes"] for s in subset]
        el = [s["n_edges_linear"] for s in subset]
        en = [s["n_edges_nonlinear"] for s in subset]
        print(f"\n节点数        均值 {sum(nn)/len(nn):.2f}  [{min(nn)}, {max(nn)}]   <- 两条件完全相同")
        print(f"线性版边数    均值 {sum(el)/len(el):.2f}")
        print(f"非线性版边数  均值 {sum(en)/len(en):.2f}")
        print(f"两版边数相同的记录: {sum(1 for s in subset if s['n_edges_linear'] == s['n_edges_nonlinear'])} / {len(subset)}")
        from collections import Counter
        print("非线性结构构成:", dict(Counter(structures.combo_label(s["nonlinear_type_cnt"]) for s in subset)))
    store.atomic_write_json(subset, args.subset_file)
    print(f"\n[已写入] {args.subset_file}")
    return 0


# ---------------------------------------------------------------- predict

def cmd_predict(args):
    subset = store.load_json_safe(args.subset_file)
    if not subset:
        print(f"未找到 {args.subset_file}，请先运行 `subset`。")
        return 1
    if args.limit:
        subset = subset[:args.limit]

    records = [] if args.restart else (store.load_json_safe(args.checkpoint) or [])
    done_ids = {r["id"] for r in records}
    todo = [s for s in subset if s["id"] not in done_ids]
    print(f"待处理 {len(todo)} / {len(subset)} 条（已完成 {len(records)}）")

    if todo:
        client = llm.get_client(args.base_url, args.api_key_env,
                                llm.build_headers(args.base_url, args.header))
        protocol = args.protocol
        if protocol == "auto":
            print("\n[协议探测] 确认该模型支持哪种调用协议 ...")
            protocol = llm.detect_protocol(client, args.model)
            if protocol is None:
                print("\n[!] 该模型两种协议都不可用，已中止。")
                return 3
        template = open(args.prompt, "r", encoding="utf-8").read()
        for i, item in enumerate(todo, 1):
            rid = item["id"]
            print(f"[{i}/{len(todo)}] id={rid} ...", end=" ", flush=True)
            raw = None
            try:
                raw = llm.call_model(
                    client, args.model, template,
                    json.dumps({"id": rid, "scenario": item["scenario"],
                                "unordered_nodes": item["unordered_nodes"]},
                               ensure_ascii=False, indent=2),
                    reasoning_effort=args.reasoning_effort or None,
                    enable_thinking=not args.no_thinking,
                    protocol=protocol,
                    max_retries=args.max_retries, retry_delay=args.retry_delay)
                gen = llm.extract_json(raw)
                if "edges" not in gen or "script_graph" not in gen:
                    raise ValueError("输出缺少 edges 或 script_graph")
                ref = item["linear_gold"]
                em_e, em_s = metrics.compare_graphs(gen["edges"], gen["script_graph"],
                                                    ref["edges"], ref["script_graph"])
                rec = {
                    "id": rid, "scenario": item["scenario"],
                    "unordered_nodes": item["unordered_nodes"],
                    "edges": gen["edges"], "script_graph": gen["script_graph"],
                    "edges_match": em_e, "sg_match": em_s,
                    **metrics.compute_edge_metrics(gen["edges"], ref["edges"]),
                }
                print(f"ok (EM={em_e and em_s})")
            except Exception as exc:                  # noqa: BLE001
                rec = {
                    "id": rid, "scenario": item["scenario"],
                    "unordered_nodes": item["unordered_nodes"],
                    "edges": [], "script_graph": {"type": "sequence", "script": []},
                    "edges_match": False, "sg_match": False,
                    **metrics.compute_edge_metrics([], item["linear_gold"]["edges"]),
                    "error": f"{type(exc).__name__}: {exc}", "raw": raw,
                }
                print(f"失败: {type(exc).__name__}")
            records.append(rec)
            store.atomic_write_json(records, args.checkpoint)
            time.sleep(args.sleep)

    store.atomic_write_json(records, args.linear_results)
    print(f"\n[已写入] {args.linear_results}（{len(records)} 条）")
    return 0


# ---------------------------------------------------------------- analyze

def paired_permutation(non_vals, lin_vals, n_perm, seed):
    d = [n - l for n, l in zip(non_vals, lin_vals)]
    if not d:
        return 0.0, float("nan")
    obs = sum(d) / len(d)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        s = 0.0
        for v in d:
            s += v if rng.getrandbits(1) else -v
        if abs(s / len(d)) >= abs(obs) - 1e-12:
            hits += 1
    return obs, hits / n_perm


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def cmd_analyze(args):
    subset = {s["id"]: s for s in (store.load_json_safe(args.subset_file) or [])}
    if not subset:
        print(f"未找到 {args.subset_file}，请先运行 `subset`。")
        return 1
    lin_recs = {r["id"]: r for r in (store.load_json_safe(args.linear_results) or [])}
    non_recs = {r["id"]: r for r in (store.load_json_safe(args.nonlinear_results) or [])}
    if not lin_recs:
        print(f"未找到 {args.linear_results}，请先运行 `predict`。")
        return 1
    if not non_recs:
        print(f"未找到非线性结果 {args.nonlinear_results}")
        return 1

    # 非线性结果可能来自旧版本，统一从原始边集重算指标
    gold_records, reference_graphs, _, _ = dataset.load_gold(args.ground_truth)
    gold_by_id = {it["id"]: it for it in gold_records}

    def prep_nonlinear(rec):
        rid = rec["id"]
        gold = reference_graphs[rid]
        _, sg_match = metrics.compare_graphs(rec.get("edges"), rec.get("script_graph"),
                                             gold["edges"], gold["script_graph"])
        out = dict(rec)
        out.update(metrics.compute_edge_metrics(rec.get("edges"), gold["edges"]))
        out["em_joint"] = (out["ged"] == 0) and sg_match
        return out

    def prep_linear(rec):
        rid = rec["id"]
        ref = subset[rid]["linear_gold"]
        _, sg_match = metrics.compare_graphs(rec.get("edges"), rec.get("script_graph"),
                                             ref["edges"], ref["script_graph"])
        out = dict(rec)
        out.update(metrics.compute_edge_metrics(rec.get("edges"), ref["edges"]))
        out["em_joint"] = (out["ged"] == 0) and sg_match
        return out

    common = sorted(set(subset) & set(lin_recs) & set(non_recs))
    if not common:
        print("两个条件没有可配对的样本。")
        return 1

    lines = []

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("gbk", errors="replace").decode("gbk"))
        lines.append(s)

    out("# 固定节点对照：同一批节点，线性版 vs 非线性版\n")
    out(f"- 子集：`{args.subset_file}`（{len(subset)} 条）")
    out(f"- 线性条件预测：`{args.linear_results}`")
    out(f"- 非线性条件预测：`{args.nonlinear_results}`")
    out(f"- 可配对样本：**{len(common)}** 条；置换次数 {args.permutations}，种子 {args.seed}\n")

    pairs = [(subset[i], prep_linear(lin_recs[i]), prep_nonlinear(non_recs[i])) for i in common]

    def group_table(title, pred):
        rows = [p for p in pairs if pred(p)]
        if not rows:
            return
        out(f"### {title}（n={len(rows)}）\n")
        out("| 条件 | Exact Match (joint) | F1 | IoU | GED |")
        out("|---|---|---|---|---|")
        for idx, name in ((1, "线性版（proScript 原版金标）"), (2, "非线性版（改写后金标）")):
            rs = [p[idx] for p in rows]
            em = _mean([1.0 if r["em_joint"] else 0.0 for r in rs])
            k = sum(1 for r in rs if r["em_joint"])
            lo, hi = metrics.wilson_interval(k, len(rs))
            out(f"| {name} | {em*100:.1f}% [{lo*100:.1f},{hi*100:.1f}] | "
                f"{_mean([r['f1'] for r in rs]):.4f} | {_mean([r['iou'] for r in rs]):.4f} | "
                f"{_mean([r['ged'] for r in rs]):.3f} |")
        out("")
        out("| 指标 | 观测差值（非线性 − 线性） | 置换检验 p |")
        out("|---|---|---|")
        for key, name in (("em_joint", "Exact Match (joint)"), ("f1", "F1"),
                          ("iou", "IoU"), ("ged", "GED"), ("nged", "NGED (GED/|E|)")):
            nv = [1.0 if p[2][key] else 0.0 for p in rows] if key == "em_joint" else [p[2][key] for p in rows]
            lv = [1.0 if p[1][key] else 0.0 for p in rows] if key == "em_joint" else [p[1][key] for p in rows]
            obs, pv = paired_permutation(nv, lv, args.permutations, args.seed)
            out(f"| {name} | {obs:+.4f} | {pv:.4g} |")
        out("")

    group_table("全部节点保持样本", lambda p: True)
    group_table("原版为纯线性链的子集（最干净的对照）", lambda p: p[0]["linear_is_pure_chain"])

    out("### 说明\n")
    out("- 两个条件的**输入节点完全相同**、样本同一批，因此差值只能归因于**结构**；")
    out("- 线性版金标由 proScript 原版边经 convert.py 确定性生成，不含 LLM 改写；")
    out("- 该子集同时消掉了规模混淆与「节点被改写」的混淆，是对"
        "「非线性本身是否更难」最干净的回答。\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[已写入] {args.out}")
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(description="固定节点对照实验",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("subcommand", choices=["subset", "predict", "analyze"])
    p.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH,
                   help="标准数据集（含 type_cnt / max_depth）")
    p.add_argument("--proscript-dev", default=DEFAULT_PROSSCRIPT_DEV)
    p.add_argument("--simple-dev", default=DEFAULT_SIMPLE_DEV)
    p.add_argument("--convert-dir", default=DEFAULT_CONVERT_DIR)
    p.add_argument("--subset-file", default=SUBSET_FILE)
    p.add_argument("--linear-results", default=LINEAR_RESULTS)
    p.add_argument("--nonlinear-results", default=None)
    p.add_argument("--checkpoint", default=LINEAR_CKPT)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--out", default=ANALYZE_OUT)
    p.add_argument("--model", default="deepseek-v4-flash")
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    p.add_argument("--header", action="append", default=None, metavar="NAME=VALUE",
                   help="附加请求头（可重复）。opencode 网关必需的 x-opencode-session 会自动补上")
    p.add_argument("--reasoning-effort", default="high")
    p.add_argument("--no-thinking", action="store_true",
                   help="不发送 thinking 参数（部分模型不支持）")
    p.add_argument("--protocol", choices=["auto", "chat", "responses"], default="auto",
                   help="调用协议；auto 会自动探测（grok 系列只支持 responses）")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=2.0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=1.0)
    p.add_argument("--restart", action="store_true")
    p.add_argument("--permutations", type=int, default=10000)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    for attr in ("ground_truth", "proscript_dev", "simple_dev", "convert_dir",
                 "subset_file", "linear_results", "checkpoint", "prompt", "out"):
        setattr(args, attr, resolve(getattr(args, attr)))
    if args.nonlinear_results:
        args.nonlinear_results = resolve(args.nonlinear_results)

    if args.subcommand == "subset":
        return cmd_subset(args)
    if args.subcommand == "predict":
        return cmd_predict(args)
    if not args.nonlinear_results:
        print("analyze 需要 --nonlinear-results 指定非线性条件的结果文件。")
        return 1
    return cmd_analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())
