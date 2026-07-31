# Non-Sequence

Non-Sequence is a research repository for studying procedural event understanding beyond a single linear order. It represents procedures as directed event graphs and structured JSON programs containing `sequence`, `select`, `loop`, and `and_join` control structures. The repository also includes Loop Termination Judgment (LTJ), a discriminative task that asks whether a described state implies that a procedural loop should stop, continue, or is unaffected.

This is a research artifact, not a production workflow engine. Dataset construction uses language models and human review.

## Repository workflow

1. Reduce ProScript records to scenarios, events, and reference edges.
2. Convert linear and partially ordered procedures into the Non-Sequence JSON schema.
3. Correct graph inconsistencies and record changes.
4. Introduce and validate `select`, `loop`, `and_join` structures where semantically appropriate.
5. Predict `edges` and `script graph` from only the scenario and unordered events.
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
- LTJ : 715 sequence candidates → 180 loop candidates → 122 quality-filtered candidates → 115 final items.

The active data is under `data/`. Prompts are under `prompts/`. 

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
