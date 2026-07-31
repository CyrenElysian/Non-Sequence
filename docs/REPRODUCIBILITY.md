# Reproducibility

## Environment

1. Use Python 3.10 or newer.
2. Create an isolated environment and run `pip install -e .`.
3. Copy `.env.example` to `.env` and provide credentials locally.
4. Select JSON manifests from `configs/models/`, `configs/tasks/`, and `configs/runs/`, then pass their values through the documented CLI arguments.

Do not place API keys in JSON configurations, logs, result files, or version control.

## Suggested execution order

```bash
nonsequence-cut
nonsequence-convert
nonsequence-correct-edges --model deepseek-v4-pro
nonsequence-select-loop --model deepseek-v4-pro
nonsequence-check --model deepseek-v4-pro
nonsequence-count-structure
nonsequence-predict --model deepseek-v4-pro
nonsequence-evaluate
```

For LTJ:

```bash
nonsequence-ltj-filter-sequences
nonsequence-ltj-filter-loops
nonsequence-ltj-filter-quality
nonsequence-ltj-generate
nonsequence-ltj-judge
```

Run each command with `--help` before a full experiment. Model-assisted stages require the environment variable configured by `--api-key-env`; offline preprocessing and evaluation do not.
