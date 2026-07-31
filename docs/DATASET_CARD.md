# Dataset Card

## Overview

Non-Sequence studies procedural event understanding when a procedure is not adequately represented by one total order. The repository contains two related research tasks:

1. **CtrlScript graph generation**: recover directed edges and a structured control-flow program from a scenario and an unordered set of events.
2. **Loop Termination Judgment (LTJ)**: classify whether a state description means that a designated loop should stop (`0`), continue (`1`), or is irrelevant to the decision (`2`).

## Confirmed sizes

- **ProScript source splits:** 3,252 train, 1,085 development, and 2,077 test records.
- **CtrlScript:** 1,061 records.
- **LTJ funnel:** 715 sequence candidates → 180 loop candidates → 122 quality-filtered candidates → 115 final items.

The arrows in the LTJ count describe filtering stages, not train/dev/test splits.

## CtrlScript schema

Each record contains:

- `id`: record identifier.
- `scenario`: procedural goal or scenario.
- `unordered_nodes`: mapping from node IDs to event descriptions.
- `edges`: directed edges written as `source->target`.
- `script_graph`: nested JSON representation of control flow.
- Optional structural statistics such as maximum nesting depth and per-type counts.

Supported structures are:

- `sequence`: ordered execution via `script`.
- `select`: mutually exclusive alternatives via `options`; branches reconverge.
- `loop`: an `entry`, a `retry` body, and an `exit`.
- `and_join`: internally ordered branches that may interleave, all of which must finish before a shared continuation.

Graph completeness, connectivity, and agreement between `edges` and `script_graph` are intended invariants.

## LTJ schema

Intermediate LTJ records include a `scenario`, ordered `steps`, `loop_idx`, and `loop_step`. Final judgment examples additionally contain state descriptions paired with:

- `0`: stop or exit the loop.
- `1`: continue the loop.
- `2`: irrelevant to the loop decision.

Difficulty tags use `easy`, `medium`, `hard`, and `na`; `na` is reserved for irrelevant descriptions. These tags reflect generation prompts and review conventions, not a calibrated psychometric scale.

## Construction and provenance

The graph task begins with ProScript procedures, converts them to the repository schema, applies LLM-assisted correction and restructuring, and adds non-linear control structures where appropriate. LTJ starts from sequence-only candidates, filters for implicit loop semantics and quality, then generates state descriptions for termination judgments. 

## Data locations

- Active datasets: `data/`
- Prompt templates: `prompts/`
- Published CtrlScript predictions and summaries: `results/ctrlscript/`
