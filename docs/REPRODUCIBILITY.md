# Reproducibility

## Environment

1. Use Python 3.10 or newer.
2. Create an isolated environment and run `pip install -e .`.
3. Copy `.env.example` to `.env` and provide credentials locally.
4. Select JSON manifests from `configs/models/`, `configs/tasks/`, and `configs/runs/`, then pass their values through the documented CLI arguments.

Do not place API keys in JSON configurations, logs, result files, or version control.

## Run record

For every model-assisted stage, preserve:

- exact model identifier and endpoint/provider;
- date and time of the request;
- prompt file and task configuration;
- temperature, top-p, token limit, seed if supported, and retry policy;
- input data path and record count;
- raw response, parsed response, validation failures, and repair attempts;
- output path and any manual or automated change log.

The aliases `flash` and `pro` are convenience labels, not immutable model versions. The example configs preserve the identifiers used by the existing experiments; replace them with provider-supported pinned identifiers when reproducibility across provider revisions is required.

## Suggested execution order

```bash
nonsequence-cut
nonsequence-convert
nonsequence-correct-edges --model deepseek-v4-flash
nonsequence-select-loop --model deepseek-v4-flash
nonsequence-check --model deepseek-v4-pro
nonsequence-count-structure
nonsequence-predict --model deepseek-v4-flash
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

## Verification checklist

- Confirm input counts before each stage.
- Validate every generated artifact as JSON.
- Check that all edge endpoints exist in `unordered_nodes`.
- Check agreement between `edges` and `script_graph`.
- Ensure predictions use only allowed task inputs.
- Recompute summaries from per-record results.
- Compare the aggregate sample count with CtrlScript's 1,061 records.
- Never create or report LTJ results unless an actual evaluation was run.

## Sources of nondeterminism

Hosted models can change behind a stable alias, and some endpoints do not guarantee deterministic sampling. Retries, safety filters, JSON repair, and concurrent requests can also alter outputs. A reproducible release should therefore retain immutable raw responses and document provider revisions in addition to setting a seed when available.
