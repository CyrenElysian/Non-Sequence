# Evaluation

## Graph generation

Let \(G\) be the reference edge set and \(P\) the predicted edge set.

- **Edge exact match:** \(P = G\).
- **Structured-graph exact match:** normalized predicted `script_graph` equals the normalized reference.
- **Joint exact match:** both exact-match conditions hold.
- **Precision:** \(|P \cap G| / |P|\).
- **Recall:** \(|P \cap G| / |G|\).
- **F1:** harmonic mean of precision and recall.
- **IoU (Jaccard):** \(|P \cap G| / |P \cup G|\).
- **GED:** edge edit distance with unit-cost deletion and insertion, reported as `e_del + e_ins`.

Define zero-denominator behavior explicitly in code and use it consistently across runs. Edge order must not affect edge metrics. Structured JSON should be normalized before exact comparison so that whitespace and object-key order do not create false errors; list order remains semantically significant.

Report the number of examples beside every aggregate. The published summaries include overall metrics and breakdowns by maximum nesting depth, contained structure type, and exact structure combination. Small groups should be interpreted cautiously.

## LTJ

LTJ is a three-class classification task:

- `0`: stop/exit.
- `1`: continue.
- `2`: irrelevant.

Recommended reporting includes overall accuracy, per-class precision/recall/F1, a confusion matrix, and accuracy by difficulty (`easy`, `medium`, `hard`, `na`). Because difficulty is assigned by generation procedure, comparisons across levels are descriptive rather than calibrated.

No public LTJ result is currently documented. Do not infer or reconstruct one from intermediate data.

## Interpretation

Exact match measures strict recovery but can reject semantically acceptable alternative event graphs. P/R/F1 and IoU provide partial credit for overlapping edges; GED measures the number of edge edits needed to recover the reference. None of these metrics alone resolves genuine ambiguity, so qualitative error analysis remains important.

See [Results](RESULTS.md) for the published CtrlScript aggregates.
