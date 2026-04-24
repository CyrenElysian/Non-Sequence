### Non-Sequence

1. 利用`cut.py`对proscript原始数据集进行修改，仅预留"scenario"，"events","gold_edges_for_prediction"
2. 利用`covert.py` 将proscript_simple中的数据转化为我们所定义的JSON结构（详见`comprehensive.md`）
3. 利用`correct_edges`调用大模型API 修改 converted_dev.json等数据的逻辑错误，同时生成修改日志，目前对`temp`（20条）进行了评估，详见`evaluate.md`