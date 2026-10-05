"""CtrlScript 图生成评测 —— 生成 + 评测 + 汇总（取代旧 predict/predict.py）。

相比旧脚本的修正
----------------
1. **修掉崩溃**：旧 `node_report` 把 list 当 set 用（`used = []` 却调用 `.add`），
   导致 `evaluate_item` 在第一条记录就抛 AttributeError、整个脚本无法运行。
   这里改用 cslib.structures.audit_nodes（已做变异测试）。
2. **金标不再依赖预生成的 stats 文件**：v3 没有 type_cnt / max_depth，
   旧脚本把路径写死指向 v1 的 stats，改指向 v3 会静默把全部记录判成 linear、
   深度全为 0。这里缺失时就地计算（口径同 count_structure.py）。
3. **加入重试与指数退避**：旧版无重试，一次 429/超时即成为永久失败记录。
4. **原子写 checkpoint + 损坏容忍**：旧版每条全量重写且非原子，中断即报废。
5. **实验溯源**：记录 model / 提示词指纹 / 金标指纹到 run manifest。
6. **去掉 `_ref_edges`**：比率指标的分母直接取当前金标，避免金标修订后口径脱钩。
7. **失败样本判定修正**：失败记录的 edges_match / sg_match 强制为 False
   （旧版在金标无边集时会把"空预测"算成完全正确）。
8. **写盘顺序修正**：先按当前金标 refresh，再写 results（旧版先写后 refresh）。
9. **结构合法性统计**：逐条记录模型输出的结构错误，可区分
   "结构非法" 与 "结构合法但选错"（对应审稿人要求的 structural-validity rate）。
10. **CLI 参数化**：换模型 / 换提示词 / 换金标不再需要改源码。
11. 去掉重复计算（旧版同一条记录的边指标被算了 2–3 次）与未使用的 import。

用法
----
    cd predict_new
    python run_generation.py --dry-run                     # 只体检金标，不调 API
    python run_generation.py --limit 5                     # 小样本试跑
    python run_generation.py                               # 全量
    python run_generation.py --model deepseek-v4-pro --tag v4-pro

"""

# python run_generation.py
# --model deepseek-v4.1-flash
# --base-url https://api.deepseek.com
# --api-key-env DEEPSEEK_API_KEY
# --tag --api-key-env v4.1-flash

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cslib import dataset, llm, metrics, store, structures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_GROUND_TRUTH = dataset.DEFAULT_GROUND_TRUTH
DEFAULT_PROMPT = os.path.join(HERE, "prompts", "prompt_v2.txt")


# ---------------------------------------------------------------- 路径

def resolve(path, base=HERE):
    return path if os.path.isabs(path) else os.path.join(base, path)


def derive_prefix(args):
    """输出文件名前缀：默认由 tag 或 model+prompt 派生。"""
    if args.tag:
        return os.path.join(HERE, f"run_{args.tag}")
    prompt_stem = os.path.splitext(os.path.basename(args.prompt))[0]
    model_tag = args.model.replace("/", "-")
    return os.path.join(HERE, f"run_{model_tag}_{prompt_stem}")


# ---------------------------------------------------------------- 评测单条

def evaluate(gen, item, reference_graphs):
    """对一条模型输出做完整评测（图比对 + 边指标 + 节点审计 + 结构校验）。"""
    rid = item["id"]
    gold = reference_graphs[rid]
    pred_edges = gen.get("edges")
    pred_sg = gen.get("script_graph")

    edges_match, sg_match = metrics.compare_graphs(
        pred_edges, pred_sg, gold["edges"], gold["script_graph"])
    edge_metrics = metrics.compute_edge_metrics(pred_edges, gold["edges"])
    audit = structures.audit_nodes(item["unordered_nodes"].keys(), pred_sg)
    report = structures.validate(pred_sg)

    return {
        "id": rid,
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"],
        "edges": pred_edges,
        "script_graph": pred_sg,
        "edges_match": edges_match,
        "sg_match": sg_match,
        "structure_valid": report.ok,
        "structure_errors": report["errors"],
        "structure_deviations": report["deviations"],
        **audit,
        **edge_metrics,
    }


def failed_record(item, error, raw):
    """生成失败样本：指标按空预测计算，但正确性一律记 False。"""
    return {
        "id": item["id"],
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"],
        "edges": [],
        "script_graph": {"type": "sequence", "script": []},
        "edges_match": False,
        "sg_match": False,
        "structure_valid": False,
        "structure_errors": ["模型调用或解析失败"],
        "structure_deviations": [],
        "nodes_valid": False,
        "nodes_missing": sorted(item["unordered_nodes"].keys(), key=lambda x: int(x) if x.isdigit() else 0),
        "nodes_extra": [],
        "nodes_duplicated": [],
        "nodes_over_duplicated": [],
        "nodes_unexpected_dup": [],
        **metrics.compute_edge_metrics([], []),
        "error": error,
        "raw": raw,
    }


# ---------------------------------------------------------------- 报告与门禁

def print_failure_report(records, limit=20):
    """列出调用/解析失败的样本——它们按空预测计入分母，必须显式披露。"""
    failed = [r for r in records if r.get("error")]
    if not failed:
        print("\n模型调用失败样本: 0 条")
        return failed
    print(f"\n[!] 模型调用/解析失败样本: {len(failed)} 条（按空预测计入分母）")
    for r in failed[:limit]:
        print(f"    id={r['id']}  {str(r.get('error'))[:150]}")
    if len(failed) > limit:
        print(f"    ...（另有 {len(failed) - limit} 条）")
    return failed


def failure_gate(records, threshold=0.05):
    """失败率门禁：超过阈值时调用方应给出非零退出码，避免把网络故障当成实验结论。"""
    total = len(records)
    n_failed = sum(1 for r in records if r.get("error"))
    rate = (n_failed / total) if total else 0.0
    return {"n_failed": n_failed, "n_total": total,
            "rate": rate, "threshold": threshold, "ok": rate <= threshold}


# ---------------------------------------------------------------- 主流程

def build_parser():
    p = argparse.ArgumentParser(
        description="CtrlScript 图生成评测（生成 + 评测 + 汇总）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH,
                   help="标准数据集（含 type_cnt / max_depth）")

    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="提示词文件")
    p.add_argument("--model", default="deepseek-v4-flash", help="模型名")
    p.add_argument("--base-url", default="https://api.deepseek.com", help="API base url")
    p.add_argument("--api-key-env", default="DEEPSEEK_API_KEY", help="存放 API key 的环境变量名")
    p.add_argument("--header", action="append", default=None, metavar="NAME=VALUE",
                   help="附加请求头（可重复）。opencode 网关必需的 x-opencode-session 会自动补上")
    p.add_argument("--tag", default=None, help="输出文件名标签；默认由 model+prompt 派生")
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 条（试跑）")
    p.add_argument("--sleep", type=float, default=1.0, help="每条之间的间隔秒数")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--retry-delay", type=float, default=2.0)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--reasoning-effort", default="high",
                   help="置空字符串则不加该参数（部分模型不支持，传了会报 400）")
    p.add_argument("--no-thinking", action="store_true",
                   help="不发送 thinking 参数（部分模型不支持）")
    p.add_argument("--protocol", choices=["auto", "chat", "responses"], default="auto",
                   help="调用协议；auto 会在跑全量前自动探测一次"
                        "（grok 系列只支持 responses，glm 只支持 chat）")
    p.add_argument("--checkpoint-every", type=int, default=10)
    p.add_argument("--restart", action="store_true", help="忽略已有 checkpoint 重新开始")
    p.add_argument("--retry-failed", action="store_true",
                   help="续跑时把 checkpoint 里已失败的样本重新排队（否则会被当作已完成跳过）")
    p.add_argument("--max-consecutive-failures", type=int, default=3,
                   help="连续失败达到该条数即中止运行并保存（0 关闭）；防止配额耗尽后空转")
    p.add_argument("--fail-threshold", type=float, default=0.05,
                   help="失败率超过该值则以非零状态退出")
    p.add_argument("--dry-run", action="store_true", help="只体检金标与计划，不调用 API")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.prompt = resolve(args.prompt)
    args.ground_truth = resolve(args.ground_truth)
    prefix = derive_prefix(args)

    ckpt_path = prefix + ".checkpoint.json"
    results_path = prefix + ".results.json"
    summary_path = prefix + ".summary.json"
    manifest_path = prefix + ".manifest.json"

    # ---------- 金标
    gold_records, reference_graphs, reference_stats, gold_meta = dataset.load_gold(args.ground_truth)
    prompt_info = llm.prompt_meta(args.prompt)
    headers = llm.build_headers(args.base_url, args.header)

    print("=" * 78)
    print(f"金标    : {gold_meta['file']}")
    if gold_meta["stats_from_file"]:
        note = (f"stats=file（已逐条复算校验，不一致 {gold_meta['stats_mismatch']} 条）")
    else:
        note = f"stats=computed（就地计算 {gold_meta['stats_computed']} 条）"
    print(f"          {gold_meta['n']} 条  sha256={gold_meta['sha256'][:16]}  {note}")
    print(f"提示词  : {prompt_info['file']}")
    print(f"          sha256={prompt_info['sha256_16']}  {prompt_info['chars']} chars")
    print(f"模型    : {args.model}  @ {args.base_url}")
    print(f"请求头  : {sorted(headers) if headers else '（无）'}")
    print(f"输出前缀: {prefix}")
    print("=" * 78)

    # ---------- 金标自检（结构 + 边一致性）
    if args.dry_run:
        print("\n[dry-run] 金标结构自检 ...")
        bad_struct, bad_edges = [], []
        for item in gold_records:
            rep = structures.validate(item["script_graph"])
            if not rep.ok:
                bad_struct.append((item["id"], rep["errors"][:2], rep["deviations"][:2]))
            diff = structures.edge_diff(item["edges"], _implied(item))
            if not diff["ok"]:
                bad_edges.append((item["id"], sorted(diff["missing"]), sorted(diff["extra"])))
        print(f"  结构不合法: {len(bad_struct)} 条")
        for rid, errs, devs in bad_struct[:10]:
            print(f"    id={rid} errors={errs} deviations={devs}")
        print(f"  边集与结构不一致: {len(bad_edges)} 条")
        for rid, miss, extra in bad_edges[:10]:
            print(f"    id={rid} 缺={miss} 多={extra}")
        n_lin = sum(1 for s in reference_stats.values()
                    if structures.nonlinearity_label(s["type_cnt"]) == "linear")
        print(f"\n  线性 {n_lin} / 非线性 {len(gold_records) - n_lin}")
        print(f"  计划处理 {min(args.limit or len(gold_records), len(gold_records))} 条")
        return 0

    # ---------- 恢复
    records = [] if args.restart else (store.load_json_safe(ckpt_path) or [])
    valid_ids = set(reference_stats)
    if records:
        before = len(records)
        records = [r for r in records if r.get("id") in valid_ids]
        dropped = before - len(records)
        n_failed = sum(1 for r in records if r.get("error"))
        if args.retry_failed and n_failed:
            records = [r for r in records if not r.get("error")]
            print(f"从 checkpoint 恢复 {before} 条（丢弃无效 {dropped} 条，"
                  f"失败 {n_failed} 条重新排队，保留 {len(records)} 条）")
        else:
            print(f"从 checkpoint 恢复 {before} 条（有效 {len(records)} 条）")
            if n_failed:
                print(f"  [!] 其中 {n_failed} 条是失败样本，默认会被跳过。"
                      f"若要重试这些样本，请加 --retry-failed")
        metrics_refresh(records, reference_graphs)

    todo = [it for it in gold_records if it["id"] not in {r["id"] for r in records}]
    if args.limit:
        todo = todo[:args.limit]

    print(f"\n待处理 {len(todo)} 条（已完成 {len(records)} 条）")
    protocol = args.protocol
    if not todo:
        print("没有需要处理的样本。")
    else:
        client = llm.get_client(args.base_url, args.api_key_env, headers)

        # auto：先探测一次该模型可用哪种协议，避免每条样本都试错（会把请求数翻倍）
        if protocol == "auto":
            print("\n[协议探测] 用极小请求确认该模型支持哪种调用协议 ...")
            protocol = llm.detect_protocol(client, args.model)
            if protocol is None:
                print("\n[!] 该模型在两种协议下都不可用，已中止（未写任何失败记录）。")
                print(f"    模型名是否写对？可用 `--base-url` 对应网关的模型列表核对。")
                return 3
        else:
            print(f"\n[协议] 使用 {protocol}（由 --protocol 指定）")

        template = open(args.prompt, "r", encoding="utf-8").read()
        done = 0
        consecutive = 0
        aborted = None
        try:
            for item in todo:
                rid = item["id"]
                print(f"[{done + 1}/{len(todo)}] id={rid} ...", end=" ", flush=True)
                raw = None
                try:
                    raw = llm.call_model(
                        client, args.model, template, build_user_message(item),
                        reasoning_effort=args.reasoning_effort or None,
                        enable_thinking=not args.no_thinking,
                        protocol=protocol,
                        max_retries=args.max_retries, retry_delay=args.retry_delay,
                        max_tokens=args.max_tokens)
                    gen = llm.extract_json(raw)
                    if "edges" not in gen or "script_graph" not in gen:
                        raise ValueError("输出缺少 edges 或 script_graph")
                    rec = evaluate(gen, item, reference_graphs)
                    consecutive = 0
                    print(f"ok (EM={rec['edges_match'] and rec['sg_match']})")
                except llm.FatalAPIError as exc:
                    # 配额耗尽 / 认证失败：立刻停，不写失败记录（续跑时这条会自动重试）
                    print(f"终止: {str(exc)[:140]}")
                    aborted = f"API 不可用 —— {exc}"
                    break
                except Exception as exc:              # noqa: BLE001
                    rec = failed_record(item, f"{type(exc).__name__}: {exc}", raw)
                    consecutive += 1
                    print(f"失败: {type(exc).__name__}（连续 {consecutive} 次）")

                records.append(rec)
                done += 1
                if done % max(1, args.checkpoint_every) == 0:
                    store.atomic_write_json(records, ckpt_path)
                if args.max_consecutive_failures and consecutive >= args.max_consecutive_failures:
                    aborted = f"连续 {consecutive} 条失败"
                    break
                time.sleep(args.sleep)
        except KeyboardInterrupt:
            print("\n[中断] 保存 checkpoint ...")
            store.atomic_write_json(records, ckpt_path)
            print(f"已保存 {len(records)} 条到 {ckpt_path}")
            return 130

        if aborted:
            store.atomic_write_json(records, ckpt_path)
            n_failed = sum(1 for r in records if r.get("error"))
            print()
            print("!" * 78)
            print(f"[!] 运行已中止：{aborted}")
            print(f"    已保存 {len(records)} 条到 {ckpt_path}")
            print(f"    其中失败样本 {n_failed} 条")
            print(f"    恢复：等配额恢复后用**相同的 --tag** 重跑，会自动从断点继续；")
            print(f"          若想连失败样本一起重试，再加上 --retry-failed")
            print("!" * 78)
            return 3

    # ---------- 先按当前金标 refresh，再写盘（修正旧版的写盘顺序）
    metrics_refresh(records, reference_graphs)
    store.atomic_write_json(records, results_path)
    store.atomic_write_json(records, ckpt_path)

    summary = metrics.compute_statistics(records, reference_stats, reference_graphs)
    print()
    metrics.print_summary(summary)
    print_failure_report(records)
    store.atomic_write_json(summary, summary_path)

    gate = failure_gate(records, args.fail_threshold)
    store.save_manifest({
        "model": args.model,
        "base_url": args.base_url,
        "headers": sorted(headers),
        "protocol": protocol,
        "prompt": prompt_info,
        "ground_truth": gold_meta,
        "n_records": len(records),
        "failure_gate": gate,
        "max_retries": args.max_retries,
        "sleep": args.sleep,
        "reasoning_effort": args.reasoning_effort,
        "files": {"checkpoint": ckpt_path, "results": results_path,
                  "summary": summary_path},
    }, manifest_path)

    print(f"\n结果   : {results_path}")
    print(f"汇总   : {summary_path}")
    print(f"清单   : {manifest_path}")
    if not gate["ok"]:
        print(f"\n[!] 失败率 {gate['rate']*100:.1f}% 超过阈值 {gate['threshold']*100:.1f}%"
              f"（{gate['n_failed']}/{gate['n_total']}）。"
              f"请重跑失败样本后再使用该结果。")
        return 2
    return 0


def _implied(item):
    """由 script_graph 推导边集并转回字符串形式，便于 edge_diff 比较。"""
    return [f"{a}->{b}" for a, b in structures.derive_edges(item["script_graph"])]


def metrics_refresh(records, reference_graphs):
    """按当前金标重算每条记录的边指标与 edges_match（不重调 API）。"""
    for r in records:
        rid = r.get("id")
        if rid not in reference_graphs:
            continue
        gold_edges = reference_graphs[rid]["edges"]
        r.update(metrics.compute_edge_metrics(r.get("edges"), gold_edges))
        if not r.get("error"):
            r["edges_match"] = r["ged"] == 0
    return records


def build_user_message(item):
    return json.dumps(
        {"id": item["id"], "scenario": item["scenario"],
         "unordered_nodes": item["unordered_nodes"]},
        ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
