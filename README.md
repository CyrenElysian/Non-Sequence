### Non-Sequence

1. **利用 **`cut.py`对proscript原始数据集进行修改，仅预留"scenario"，"events","gold\_edges\_for\_prediction"
2. **利用 **`covert.py` 将proscript\_simple中的数据转化为我们所定义的JSON结构（详见 `comprehensive.md`），其中已包含**sequence**和**and\_join**结构
3. **利用 **`correct_edges.py`调用大模型API 修改 converted\_dev.json 等数据的逻辑错误，同时生成修改日志，目前对 `temp`（20条）进行了评估，详见 `temp\evaluate.md`
4. **利用大模型API对 converted\_dev.json 数据集中的逻辑错误进行修改，得到**`convert/llm_fixed_dev.json`（近1100条数据，逻辑依然可能存在错误
5. **利用 **`select_loop.py`调用大模型API对修改后的 llm\_fixed\_dev.json 进行筛选（约1100条事件链），判断哪些事件链适合引入选择和循环结构（或者都不合适）。具体要求见`Comprehensive.md`中的约束
