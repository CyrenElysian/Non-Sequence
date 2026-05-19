`processed_data.json`: 使用 **deepseek-v4-pro** 构造的全量数据

`tmp.json`: 从`processed_data.json`中按结构权重抽取的 **10%** 的数据

`count_structure.py`: 最大嵌套深度和结构数统计脚本

**运行指令**: `python count_structure.py -i tmp.json -o tmp_stats.json`


`loop_only.json`: **14** 条节点重复和循环异常数据

`loop_only_processed.json`: **14** 条修改后的数据
