# Results

## Published CtrlScript results

The public evaluation covers all 1,061 CtrlScript records. Values below are reproduced from:

- `results/ctrlscript/eval_summary_v4-flash.json`
- `results/ctrlscript/eval_summary_v4-pro.json`

| Model label | Edge exact | Script exact | Joint exact | Precision | Recall | F1 | IoU | GED |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Flash | 44.20% (469/1,061) | 43.83% (465/1,061) | 43.83% (465/1,061) | 0.7895 | 0.7754 | 0.7811 | 0.6998 | 2.8709 |
| Pro | 47.69% (506/1,061) | 47.13% (500/1,061) | 47.13% (500/1,061) | 0.8125 | 0.8036 | 0.8068 | 0.7301 | 2.5702 |

The model names are labels inherited from the result files. They should not be interpreted as immutable provider versions unless the corresponding run metadata pins a full identifier.

## Results by maximum nesting depth

Joint exact match decreases as the reference structure becomes deeper:

| Depth | n | Flash joint exact | Pro joint exact |
|---|---:|---:|---:|
| 1 | 546 | 62.64% | 65.38% |
| 2 | 448 | 24.11% | 28.35% |
| 3+ | 67 | 22.39% | 23.88% |

The 3+ group is much smaller, so its estimate is less stable.

## Results by contained structure

These groups overlap when a record contains more than one nonlinear structure.

| Structure | n | Flash joint exact | Pro joint exact |
|---|---:|---:|---:|
| `select` | 75 | 40.00% | 40.00% |
| `loop` | 161 | 29.19% | 29.81% |
| `and_join` | 332 | 16.87% | 21.99% |

The source summaries also provide edge metrics and disjoint structure-combination breakdowns. Use those JSON files for full-precision analysis.

## LTJ status

No public LTJ evaluation result is present or claimed. The confirmed LTJ numbers (715 → 180 → 122 → 115) describe dataset filtering, not model performance.

## Caveats

Exact match can undercount plausible alternative control-flow graphs. Results are tied to the included data and evaluation implementation, and model aliases can drift over time. See [Evaluation](EVALUATION.md) and [Reproducibility](REPRODUCIBILITY.md).
