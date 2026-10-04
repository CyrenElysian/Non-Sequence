"""predict_new 共享库。

模块职责：
  structures  结构语义（遍历 / 校验 / 边推导 / 节点审计 / 深度统计）
  metrics     评测指标与分组汇总
  dataset     金标加载（stats 缺失时就地计算）
  llm         模型调用（重试）、响应解析、提示词指纹
  store       原子落盘、损坏容忍、run manifest

设计约束：所有实验脚本共用本库，结构规则与指标公式只有一份实现。
"""

from . import dataset, llm, metrics, store, structures  # noqa: F401

__all__ = ["structures", "metrics", "dataset", "llm", "store"]
