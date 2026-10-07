# predict_new —— CtrlScript 评测与实验脚本（重写版）

本目录是 `predict/` 的**重写版本**，覆盖其全部功能并修掉已确认的问题。
**`predict/` 下的任何文件都未被改动**，两者可并存对照。

**标准数据集：`introduce/stats/CtrlScript_v3_stats.json`**（1076 条，已含 `type_cnt` / `max_depth`）。
本目录所有脚本默认使用它；该路径只在 `cslib/dataset.py` 中定义一次（`DEFAULT_GROUND_TRUTH`）。

---

## 1. 目录结构

```
predict_new/
├── cslib/                     共享库（结构规则与指标公式的唯一实现）
│   ├── structures.py          遍历 / 校验 / 边推导 / 节点审计 / 深度统计
│   ├── metrics.py             P·R·F1·IoU、GED、NGED、分组汇总
│   ├── dataset.py             金标加载（stats 缺失时就地计算）
│   ├── llm.py                 模型调用（重试）、响应解析、提示词指纹
│   └── store.py               原子落盘、损坏容忍、run manifest
├── prompts/
│   └── prompt_v2.txt          提示词（与 predict/prompt_predict_v2.txt 逐字一致）
├── run_generation.py          生成 + 评测 + 汇总（替代 predict.py）
├── stratify.py                节点数分层对照实验（替代 stratify.py）
└── fixed_nodes.py             固定节点对照实验（替代 linear_vs_nonlinear.py）
```

设计原则：

1. **单一实现**——结构语义与指标公式各只有一份，所有脚本共用，杜绝口径漂移。
2. **零 numpy 依赖**——`non_seq` 的 numpy 2.5.3 BLAS/LAPACK 链接损坏，任何矩阵乘法
   或 `np.linalg` 调用都会触发**无法被 try/except 捕获**的原生崩溃
   （`Windows fatal exception 0xc06d007f`）。随机化检验用标准库 `random`，
   回归用纯 Python 高斯消元。
3. **可复现**——配置全部走 CLI；每次运行产出 run manifest（模型、提示词指纹、金标指纹）。
4. **不静默失败**——失败样本显式记录与披露，并有失败率门禁。

---

## 2. 修掉的问题

### P0（原本会直接导致失败）

| 问题 | 修正 |
|---|---|
| `predict.py` 的 `node_report` 把 list 当 set 用（`used = []` 却调用 `.add`），`evaluate_item` 在**第 1 条记录即抛 AttributeError**，脚本完全无法运行 | 改用 `structures.audit_nodes`，并修掉其 `loop_related_nodes` 只收 entry/exit、**不收 retry 内部节点**的缺陷（会把合法的 loop 重复误判为违规） |
| 金标路径写死指向 v1 的 stats 文件；无 stats 的版本被指向时会**静默把所有记录判为 linear、深度全为 0** | `dataset.load_gold` 在字段缺失时**就地计算**（口径同 `count_structure.py`）；**且无论文件是否带 stats 都会逐条复算比对**，不一致则醒目告警并写入 manifest |

### stats 一致性校验

标准数据集带 `type_cnt` / `max_depth`，但脚本**不会直接采信**：
`load_gold` 会对每条记录重算一遍并与文件值比对，不一致时打印告警
（含前 5 条的具体差异）并在 manifest 的 `ground_truth.stats_mismatch` 中记录条数。

这是针对历史事故的防线——当初 v1 的 stats 被用在另一版数据上，
导致 Table 1 与全部评测分组静默错位，而运行过程没有任何报错。
现在同类问题会在运行开始时立刻暴露。

### P1（会在重跑时造成损失）

| 问题 | 修正 |
|---|---|
| `call_model` **没有任何重试**，一次 429/超时即成为永久失败记录并计入分母 | `llm.call_model` 支持 `--max-retries` + 指数退避 + 抖动 |
| checkpoint 每条**全量重写**且**非原子**；加载无异常保护，中断后每次启动都崩 | `store`：原子写（临时文件 + `os.replace` + `fsync`）+ 自动 `.bak` + 损坏时回退备份并给出可操作提示；默认每 10 条落盘一次 |
| 结果文件**没有实验溯源**，切换提示词后新旧输出会混进同一文件 | 每次运行写 run manifest |
| 记录里存 `_ref_edges` 副本；金标修订后 `refresh` 用 `setdefault` 不覆盖，导致**逐条指标用新金标、比率指标用旧金标** | 去掉该字段；比率口径分母直接取当前 `reference_graphs` |
| 失败样本在金标边集为空时会被算成"完全正确" | 失败记录的 `edges_match` / `sg_match` **强制为 False** |

### P2（正确性与可维护性）

| 问题 | 修正 |
|---|---|
| 结果文件在 `refresh` **之前**写出，落盘内容与汇总时的内存状态不同步 | 先 refresh，再写 results |
| 同一条记录的边指标被算 2–3 次（其中一次结果被直接覆盖） | 每条只算一次 |
| `extract_json` 无兜底，遇前导文字/多对象即失败 | 围栏 → 整体 → **括号配平扫描**，优先返回含 `edges`+`script_graph` 的对象 |
| 7 个 import 未使用；配置全为模块常量，换模型必须改源码 | 清理 import；全部改 CLI 参数 |
| 无 `--limit`，试提示词只能跑全量 | 加 `--limit`、`--dry-run`（金标体检，不调 API） |
| 失败率高时照样输出结果，仅打印一行清单 | 失败率超过 `--fail-threshold`（默认 5%）时以非零码退出并提示重跑 |

**新增能力**：结构合法性统计（区分"结构非法"与"结构合法但选错"）、节点审计、
EM 的 Wilson 95% 置信区间、`--dry-run` 金标全量体检。

---

## 3. 用法

脚本可在任意工作目录下运行（相对路径按脚本所在目录解析）。

### 3.1 `run_generation.py` —— 生成 + 评测 + 汇总

```bash
cd predict_new
python run_generation.py --dry-run          # 只体检金标，不调 API
python run_generation.py --limit 5          # 小样本试跑
python run_generation.py                    # 全量
python run_generation.py --model deepseek-v4-pro --tag v4-pro
```

关键参数：`--ground-truth`、`--prompt`、`--model`、`--tag`、`--limit`、`--sleep`、
`--max-retries`、`--retry-delay`、`--checkpoint-every`、`--restart`、
`--fail-threshold`、`--dry-run`、`--base-url`、`--api-key-env`、`--header`、
`--ids`、`--stream`、`--timeout`、`--connect-timeout`。

**使用 opencode 网关**（`https://opencode.ai/zen/go/v1`）时，该网关**强制要求**
`x-opencode-session` 请求头，缺失会返回 `400 MissingSessionID`。
脚本对含 `opencode` 的 `--base-url` **自动补上**一个 uuid4 会话 id
（与原 `introduce.py` / `check.py` 的做法一致），并在运行头部打印
`请求头 : ['x-opencode-session']` 以便确认：

```bash
python run_generation.py --model deepseek-v4.1-flash \
  --base-url https://opencode.ai/zen/go/v1 --api-key-env opencode_api \
  --tag v4.1-flash
```

需要固定会话 id 或追加其它头时用 `--header`（可重复，`NAME=VALUE` 形式）：

```bash
python run_generation.py --header x-opencode-session=my-fixed-id --header X-Trace=abc
```

### 调用协议：不同模型不一样（已自动处理）

opencode 网关上的模型**并非都支持 chat.completions**。实测：

| 模型 | `chat.completions` | `responses` |
|---|---|---|
| `glm-5.3` | ✅ | ❌ `ModelProtocolUnsupported` |
| `deepseek-v4.1-flash` | ✅ | ✅ |
| `grok-4.7` / `grok-4.6` | ❌ `ModelProtocolUnsupported` | ✅ |

`--protocol` 默认 `auto`：**跑全量之前用一次极小请求探测**，确定该模型走哪条路，
然后整轮沿用。既不会用错协议，也不会每条样本都试错（那会让请求数翻倍）。

```bash
# 自动探测，直接跑
python run_generation.py --model grok-4.7 --base-url https://opencode.ai/zen/go/v1 \
  --api-key-env opencode_api --tag grok-4.7

# 也可显式指定
python run_generation.py --protocol responses ...
```

探测会区分「协议不支持」（确定性，换协议）与「网络抖动」（重试，不会误判成模型不可用）。
两种协议都不通时会中止且**不写任何失败记录**。

部分模型不支持 `thinking` 参数时用 `--no-thinking` 关掉；
`--reasoning-effort` 传空字符串则完全不加该参数。

### 查询网关上有哪些模型

```python
from cslib import llm
c = llm.get_client("https://opencode.ai/zen/go/v1", "opencode_api",
                   llm.build_headers("https://opencode.ai/zen/go/v1"))
print(sorted(m.id for m in c.models.list().data))
```

`models.list()` 里**出现某个模型名不等于所选协议能用**。
上表仅描述此前 opencode 的测试结果；apinebula 上的 Grok 可以使用 `chat`，
不能把一个网关的限制推广到所有同名模型端点。

### 续跑与失败样本

中断后**用相同的 `--tag`** 重跑即从断点继续（前缀由 `--tag` 决定；
漏掉 `--tag` 会变成 `run_<model>_<prompt>` 前缀，等于从头开始）。

**失败样本默认会被跳过**（id 已在 checkpoint 中）；要重试它们加 `--retry-failed`。
重试时保留旧失败占位，返回后按 ID 替换；中断不会丢掉尚未重试的记录。
如果旧版已从 checkpoint 删除失败记录，新版会在 `--retry-failed` 时从同前缀
results 补回缺失的失败占位，不覆盖已有答案。

`--ids 742` 只生成该 ID，`--ids 742 858` 可指定多条，均保留其它 checkpoint 记录。
它不能与用于完整报告的样本筛选混淆：运行后的汇总仍包含已有记录。

### 连接错误、超时与流式接收

`APIConnectionError`、`APITimeoutError`、HTTP 524 表示请求未完成，
不能据此判定模型给出了错误答案。新版打印每次请求耗时和底层异常链，
以区分连接建立、读超时、TLS/代理或中途断开等情况。

重试仅由脚本控制，SDK 内部重试关闭；`--max-retries 3` 表示最多 4 次请求。
`--timeout` 为客户端读/写等待秒数，`--connect-timeout` 为连接建立等待秒数；
留空沿用 SDK 默认。SDK 当前默认读超时为 600 秒、连接超时为 5 秒。
网关的 524/120 秒等上游限制不受客户端超时控制。
脚本会尊重服务端数值型 `Retry-After` / `retry_after` 指示。

`--stream` 对 chat 和 Responses 都支持，只改变答案接收方式，不更改提示词或推理参数。
脚本拼接最终答案内容，不将推理文本作为答案；缺少正常结束事件或被截断的流会计为失败。
流式接收能否避开网关超时，取决于网关是否及时转发 SSE 内容；它不保证成功。

PowerShell 下可先只重试一条，避免再次等待整批嵌套重试：

```powershell
python run_generation.py `
  --model grok-4.7 `
  --base-url https://apinebula.ai/v1 `
  --api-key-env Nebula_Grok `
  --tag grok-4.7 `
  --protocol chat `
  --retry-failed --ids 742 `
  --stream --timeout 600 --connect-timeout 30 `
  --max-retries 0 --checkpoint-every 1
```

关闭当前旧进程或等其结束后再启动新命令，避免两个进程写同一 checkpoint。
单条成功后，去掉 `--ids 742` 并设置 `--max-retries 1 --retry-delay 30` 处理其余失败。
保留原推理设置；不要只为困难样本改提示词或降低推理预算后直接混入主实验。

新生成的每条记录保存实际 `generation_config`；manifest 保留历史配置。
旧记录缺少逐条配置时，新的 manifest 无法追溯重建原端点来源，需保留实际执行日志。
技术失败若最终无法恢复，完整评测必须明确披露数量及计分规则；
不可仅删除该模型的失败记录而继续与其它模型的全量指标直接比较。

另有两条保护，避免配额耗尽后空转：

| 参数 / 行为 | 说明 |
|---|---|
| `--max-consecutive-failures N` | 连续失败 N 条即中止并保存（默认 3，0 关闭） |
| 配额 / 认证 / 参数不支持类错误 | 立即抛 `FatalAPIError` 中止，**不重试、不写失败记录**，该条续跑时自动重试 |

输出（`<前缀>` 默认由 model+prompt 派生，或由 `--tag` 指定）：

```
run_<tag>.checkpoint.json    断点（原子写，自动 .bak）
run_<tag>.results.json       逐条结果
run_<tag>.summary.json       分组汇总
run_<tag>.manifest.json      实验溯源
```

Ctrl-C 会保存 checkpoint；重跑时自动续跑。

### 3.2 `stratify.py` —— 节点数分层对照

```bash
python stratify.py --results run_x.results.json --out stratify_x.md
```

只输出三张表，节点数区间固定为 `<=5`、`6`、`7`、`8`、`>=9`，不自动合并：

1. 每个区间参与统计的线性、非线性脚本数量。
2. 每个区间两组的 joint EM、精确率、召回率、F1、Jaccard、GED、NGED。
3. 每个区间的指标差值，统一为线性减非线性。

EM、精确率、召回率、F1、Jaccard 按百分数展示，其差值为百分点（pp）；
GED、NGED 及其差值使用原始数值。边指标均为逐脚本计算后的宏平均，空组显示 `n.a.`。
结果未覆盖全部金标时，报告提示缺失数量，表格只统计能按 ID 匹配的记录。

不再执行配对检验、Bootstrap 或回归。旧命令中的 `--permutations`、`--bootstrap`、
`--seed`、`--min-cell` 仍可传入，但不影响结果。旧的默认 `--bin-edges 0 5 6 7 8 99`
可兼容，其他分箱设置会被拒绝，以避免改变固定区间。

### 3.3 `fixed_nodes.py` —— 固定节点对照

```bash
python fixed_nodes.py subset                                     # 找子集（不调 API）
python fixed_nodes.py predict --limit 5                          # 试跑线性条件
python fixed_nodes.py predict
python fixed_nodes.py analyze --nonlinear-results run_x.results.json
```

同一批节点、同一批样本，只改结构：线性条件用 proScript 原版金标（由 `convert.py`
确定性生成），非线性条件用已有预测。该子集同时消掉**规模混淆**与
**"节点被改写"混淆**。

---

## 4. 环境

```bash
conda activate non_seq
```

`non_seq` 的 numpy BLAS/LAPACK 链接损坏（任何 matmul / `np.linalg` 都会触发
无法捕获的原生崩溃 `0xc06d007f`）。本目录脚本**不使用 numpy**，因此不受影响；
若其它脚本出现"进程无故退出、无任何报错"，请先修复：

```bash
conda install -n non_seq --force-reinstall numpy
```

> 命名注意：本目录下不要新建与标准库同名的文件（如 `re.py`、`bisect.py`），
> 否则会遮蔽标准库模块导致其它脚本崩溃。

---

## 5. 已知限制

1. **固定节点子集规模有限**。`fixed_nodes.py subset` 在 v3 上得到 **48 条**
   （其中原版为纯线性链的 11 条）。原因是 v3 经人工审查后大量记录的节点文本
   被改写，不再与原版逐字相同。
2. **部分记录无法回连 proScript 原版**。v3 有 17 条非线性样本因 `scenario`
   文本在改写阶段被修正过拼写而无法回连（脚本会显式计数并说明）。
   回连**只依据 scenario 文本**，不依据节点重合度——否则"节点与原版一致"
   这个子集判据会变成循环论证。
3. **NGED 是编辑率而非比例**：`NGED = GED/|E|`，可大于 1。需要"比例"时用
   `missing_rate`。论文表述必须与此一致。
