# Pipeline

## Graph-generation pipeline

### 1. Prepare ProScript

Read the confirmed ProScript splits (3,252 train / 1,085 development / 2,077 test) and retain the scenario, event descriptions, and reference prediction edges needed downstream.

### 2. Convert to the graph schema

Map events to stable node IDs, normalize edges as `source->target`, and construct a JSON `script_graph`. Initial conversions represent sequential order and join structure where supported by the source graph.

### 3. Correct graph logic

Use an LLM-assisted pass to identify spelling issues, missing or extra edges, disconnected nodes, and disagreement between edge and structured representations. Preserve a change log and validate JSON before accepting an edit.

### 4. Construct CtrlScript

Where semantics support them, introduce mutually exclusive choices and bounded loops. Rebuild both `edges` and `script_graph` together, remove superseded edges, and enforce reconvergence and connectivity. Add structural statistics such as maximum nesting depth and counts of `sequence`, `select`, `loop`, and `and_join`.

The resulting CtrlScript collection contains 1,061 records.

### 5. Predict

Give the model only `scenario` and `unordered_nodes`. Request one JSON object containing predicted `edges` and `script_graph`, then validate and normalize the response without changing its semantics.

### 6. Evaluate

Compare predictions against references with exact edge-set match, exact structured-graph match, joint exact match, edge precision/recall/F1, IoU (Jaccard), and edge GED. Aggregate overall and by nesting depth, structure type, and structure combination.

## LTJ pipeline

### 1. Extract sequence candidates

Select 715 records whose structured graph can be represented as an ordered event chain and convert them to `scenario`, `steps`, `loop_idx`, and `loop_step` candidates.

### 2. Filter for loop semantics

An LLM-assisted filter retains 180 procedures that imply repetition, waiting, retry, incremental completion, or another observable loop.

### 3. Filter for quality

Retain loops with meaningful state development and observable intermediate outcomes. This produces 122 quality-filtered candidates; subsequent review yields 115 final items.

### 4. Generate state descriptions

Generate descriptions that imply continuation or termination at several prompt-defined difficulty levels, plus contextually related but causally irrelevant descriptions. Noise augmentation must preserve the original label.

### 5. Judge and review

Hide labels and difficulty from the judging model, collect three-way predictions, and inspect disagreements. The repository does not currently publish an LTJ result set.

## Configuration

All public examples use JSON:

- `configs/models/`: provider/model and decoding settings.
- `configs/tasks/`: task inputs, outputs, prompts, and labels.
- `configs/runs/`: complete runnable compositions.

Treat configuration paths and CLI module names as provisional research interfaces until implementation consolidation is complete.
