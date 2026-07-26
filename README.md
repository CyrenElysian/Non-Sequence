# Non-Sequence

Non-Sequence is a research repository for studying procedural event understanding beyond a single linear order. It represents procedures as directed event graphs and structured JSON programs containing `sequence`, `select`, `loop`, and `and_join` control structures. The repository also includes Loop Termination Judgment (LTJ), a discriminative task that asks whether a described state implies that a procedural loop should stop, continue, or is unaffected.

This is a research artifact, not a production workflow engine. Dataset construction uses language models and human review; the annotations can contain ambiguity or residual errors.

## Repository workflow

1. Reduce ProScript records to scenarios, events, and reference edges.
2. Convert linear and partially ordered procedures into the Non-Sequence JSON schema.
3. Correct graph inconsistencies and record changes.
4. Introduce and validate selection and loop structures where semantically appropriate.
5. Predict edges and `script_graph` from only the scenario and unordered events.
6. Evaluate exact graph recovery and soft edge-level similarity.
7. Derive and filter candidate loop procedures for LTJ, generate state descriptions, and judge the three-way label.

See [Pipeline](docs/PIPELINE.md) for stage details and [Dataset Card](docs/DATASET_CARD.md) for schemas and provenance.

## Installation

Python 3.10 or newer is required.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -e .
```

Copy `.env.example` to `.env`, then provide your own API credentials. Never commit `.env` or real keys.

## Data

Confirmed collection sizes:

- ProScript: 3,252 train / 1,085 development / 2,077 test records.
- CtrlScript: 1,061 graph-generation records.
- LTJ filtering funnel: 715 sequence candidates → 180 loop candidates → 122 quality-filtered candidates → 115 final items.

The active data is under `data/`. Prompts are under `prompts/`. Large, generated, or provider-derived artifacts may have separate usage constraints; see [Dataset Card](docs/DATASET_CARD.md) before redistribution.

## CLI

The package uses a `src/` layout. Each pipeline stage has a dedicated entry point:

```bash
nonsequence-cut --help
nonsequence-convert --help
nonsequence-correct-edges --help
nonsequence-select-loop --help
nonsequence-check --help
nonsequence-count-structure --help
nonsequence-predict --help
nonsequence-evaluate --help
nonsequence-ltj-filter-sequences --help
nonsequence-ltj-filter-loops --help
nonsequence-ltj-filter-quality --help
nonsequence-ltj-generate --help
nonsequence-ltj-judge --help
```

Example JSON configurations are provided in `configs/models/`, `configs/tasks/`, and `configs/runs/` as reproducibility manifests. Use the corresponding values as explicit CLI arguments; every command documents its accepted arguments through `--help`.

## Evaluation and results

Graph generation reports edge exact match, structured-graph exact match, joint exact match, precision, recall, F1, IoU (Jaccard), and graph edit distance (GED). Public CtrlScript summaries cover all 1,061 records:

- Flash: joint exact match 43.83%, P/R/F1 0.7895/0.7754/0.7811, IoU 0.6998, GED 2.8709.
- Pro: joint exact match 47.13%, P/R/F1 0.8125/0.8036/0.8068, IoU 0.7301, GED 2.5702.

These values are read from `results/ctrlscript/eval_summary_v4-{flash,pro}.json`. No public LTJ result is reported in this repository. See [Results](docs/RESULTS.md) and [Evaluation](docs/EVALUATION.md).

## Reproducibility

Pin the model identifier, preserve the JSON configuration, record sampling and decoding settings, and retain raw responses and change logs. Hosted-model outputs may change across provider revisions even with identical settings. See [Reproducibility](docs/REPRODUCIBILITY.md).

## Citation

If you use this repository, please cite the accompanying paper. Citation metadata will be added when available.

```bibtex
@misc{nonsequence_placeholder,
  title  = {Non-Sequence: Procedural Event Graphs with Non-Linear Control Structure},
  author = {Anonymous},
  year   = {TBD},
  note   = {Citation placeholder}
}
```

## Limitations

- Some graph orderings are inherently ambiguous without temporal or causal evidence.
- LLM-assisted transformation can introduce semantic drift, malformed structures, or provider-specific bias.
- Exact match penalizes valid alternative graphs; soft metrics reduce but do not eliminate this issue.
- Nonlinear and deeply nested structures are less frequent than simple sequences.
- LTJ difficulty labels are generated heuristics and should not be treated as calibrated measures of human reasoning difficulty.
- The current public results cover CtrlScript only.

The original Chinese design drafts are retained in `docs/source/` as source notes. They document evolving ideas and may not match the finalized public interface.
