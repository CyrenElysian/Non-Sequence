### Non-Sequence

1. 利用 `cut.py`对proscript原始数据集进行修改，仅预留"scenario"，"events","gold_edges_for_prediction"
2. 利用 `covert.py` 将proscript_simple中的数据转化为我们所定义的JSON结构（详见 `comprehensive.md`），其中已包含**sequence**和**and_join**结构
3. 利用 `correct_edges.py`调用大模型API 修改 converted_dev.json 等数据的逻辑错误，同时生成修改日志，目前对 `temp`（20条）进行了评估，详见 `temp\evaluate.md`
4. 利用 `.py`调用大模型API对修改后的 converted_dev.json 进行筛选（约1000条事件链），判断哪些事件链适合引入选择和循环结构（或者都不合适）。对选择结构的要求是**存在两种或以上互斥的路径，都能达成同一目标**，但对于即时微小选择（如"用勺子还是叉子"）则是无明显意义的；对于循环结构，需要存在"重复尝试直到成功"的逻辑。对于筛选出的候选事件进行人工评判，批准50条左右，对其中的一些进行人工编写，引入相应结构，剩余部分交给大模型，并进行人工审批
