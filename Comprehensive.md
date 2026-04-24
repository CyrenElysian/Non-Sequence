除了顺序，选择，循环三种基础结构，这里再提出一种特殊结构：**同步汇合（AND-JOIN）**，这里说明放弃构建**并行**结构的原因：

- 标注者需要凭空想象时间重叠关系，容易主观臆断。虽然现实中存在大量并行结构事件，但是这种平行的结构很多时候是可以人为解释为线性，例如吃早饭和读早报可以并行执行，也可以顺序解释：先吃饭后读早报，或先读早报后吃饭均可；
- 缺乏时间戳证据的 `parallel` 在评估时无法验证，降低了数据的客观性

**同步汇合（AND-JOIN）**是弱化了**同时性**，并结合了**可交换（无序）**特点的一种“并行”结构

- **无强依赖**，事件之间无明显的时间前后和因果关系，可同时执行，可以顺序执行，也可以执行事件A一段时间后，再执行事件B；简而言之，事件之间无序，排列自由度高
- **必须全部完成**，缺少任何一项，无法执行某一步骤，导致事件图不连通

简而言之：一组事件 **无强制顺序、允许任意交错或顺序执行**，但 **必须全部完成后才能进入下一事件**。它不要求事件同时开始，仅要求全部结束后的同步。



并且我们希望设定更困难的生成式任务：根据给定的无序事件集，模型需要自己正确判断事件之间的关系，生成完整的事件图（json文件），这里先给出人工生成的示例数据：

（1)标准结构：顺序+选择+循环

```json
{
  "id": 1,
  "scenario": "online shopping",
  "unordered_nodes": {
    "0":"search for products",
    "1":"add to cart",
    "2":"choose guest login",
    "3":"choose account login (VIP)",
    "4":"enter payment password",
    "5":"incorrect password",
    "6":"payment successful",
    "7":"generate order"
  },
  "edges": [
    "0->1",
    "1->2",
    "1->3",
    "2->4",
    "3->4",
    "4->5",
    "4->6",
    "5->4",
    "6->7"
  ],
  "script_graph": {
    "type": "sequence",
    "scripts": [
      "0",
      "1",
      {
        "type": "select",
        "scripts": [
          "2",
          "3"
        ]
      },
      {
        "type": "loop",
        "entry": "4",
        "retry": [
          "5",
          "continue"
        ],
        "exit": "6"
      },
      "7"
    ]
  }
}
```

（2）同步汇合（AND-JOIN）

```json
{
  "id": 2,
  "scenario": "plan a weekend trip",
  "unordered_nodes": {
    "0":"decide destination",
    "1":"check weather forecast",
    "2":"book hotel",
    "3":"buy train tickets",
    "4":"pack luggage based on weather and hotel conditions",
    "5":"confirm final itinerary",
    "6":"set alarm for departure",
    "7":"head to station"
  },
  "edges": [
    "0->1",
    "0->2",
    "0->3",
    "1->4",
    "2->4",
    "3->4",
    "4->5",
    "5->6",
    "6->7"
  ],
  "script_graph": {
    "type": "sequence",
    "scripts": [
      "0",
      {
        "type": "and_join",
        "scripts": ["1", "2", "3"]
      },
      "4",
      "5",
      "6",
      "7"
    ]
  }
}
```



在实践过程中发现，对于**同步汇合（AND-JOIN）**的定义依然存在问题，原定义中限制了数据为**无序**，事件之间无明显的时间前后和因果关系，事实上大部分 AND‑JOIN 样本**并非完全无序**，而是包含内部线性子链（时间顺序和因果前后顺序），例如：

```json
{
  "id": 3,
  "scenario": "replicate the pasta at the restaurant",
  "unordered_nodes": {
    "0":"decided to replicate the pasta at the restaurant",
    "1":"turn on the stove",
    "2":"boil water for noodles",
    "3":"heat sauce in a pot",
    "4":"put noodles in the water",
    "5":"combine noodles and sauce",
    "6":"drain noodles over the sink",
    "7":"plate noodles and sauce together",
    "8":"replicate the pasta at the restaurant"
  },
  "edges": [
    "0->1",
    "1->2",
    "1->3",
    "2->4",
    "4->6",
    "6->5",
    "3->5",
    "5->7",
    "7->8"
  ],
  "script_graph": {
    "type": "sequence",
    "script": [
      "0",
      "1",
      {
        "type": "and_join",
        "branches_set": {
          "b1": [
            "2",
            "4",
            "6"
          ],
          "b2": [
            "3"
          ]
        }
      },
      "5",
      "7",
      "8"
    ]
  }
}
```

**`"branches_set"`**表示集合元素之间无序，即`b1` `b2`等之间无序，而对于集合中每一个branch，例如

`"b1": [ "2","4","6" ]`中"2" "4" "6"是有序的

经过改写的数据2如下所示：

```json
{
  "id": 2,
  "scenario": "plan a weekend trip",
  "unordered_nodes": {
    "0":"decide destination",
    "1":"check weather forecast",
    "2":"book hotel",
    "3":"buy train tickets",
    "4":"pack luggage based on weather and hotel conditions",
    "5":"confirm final itinerary",
    "6":"set alarm for departure",
    "7":"head to station"
  },
  "edges": [
    "0->1",
    "0->2",
    "0->3",
    "1->4",
    "2->4",
    "3->4",
    "4->5",
    "5->6",
    "6->7"
  ],
  "script_graph": {
    "type": "sequence",
    "scripts": [
      "0",
      {
        "type": "and_join",
        "branches_set": {
          "b1": ["1"],
          "b2": ["2"],
          "b3": ["3"]
        }
      },
      "4",
      "5",
      "6",
      "7"
    ]
  }
}
```

修改后的**同步回合(AND-JOIN)**的定义如下：

**同步汇合（AND-JOIN）**是弱化了**同时性**，并结合了**可交换（无序）**特点的一种“并行”结构

- **分支之间无强依赖**，分支(branchs)之间没有无明显的时间前后或因果关系，可同时执行，可以顺序执行，也可以执行分支A一段时间后，再执行分支B；简而言之，分支之间无序，排列自由度高
- **各分支必须全部完成**，缺少任何一个分支，无法执行某一步骤，导致事件图不连通
- **各个分支内的事件有逻辑顺序**，A1->A2表示分支A上的事件A1比同分支上的A2先发生

简而言之：多个分支（每个分支可包含多个时间） **无强制顺序、允许任意交错或顺序执行**，但 **必须全部完成后才能进入下一事件**。它不要求分支同时开始，仅要求全部结束后的同步。
