### 一、总体思路

1. 除了顺序，选择，循环三种基础结构，这里再提出一种特殊结构：**同步汇合（AND-JOIN）**，这里说明放弃构建**并行**结构的原因：
   - 标注者需要凭空想象时间重叠关系，容易主观臆断。虽然现实中存在大量并行结构事件，但是这种平行的结构很多时候是可以人为解释为线性，例如吃早饭和读早报可以并行执行，也可以顺序解释：先吃饭后读早报，或先读早报后吃饭均可；
   - 缺乏时间戳证据的 `parallel` 在评估时无法验证，降低了数据的客观性
2. **同步汇合（AND-JOIN）**是弱化了**同时性**，并结合了**可交换（无序）**特点的一种“并行”结构
   - **无强依赖**，事件之间无明显的时间前后和因果关系，可同时执行，可以顺序执行，也可以执行事件A一段时间后，再执行事件B；简而言之，事件之间无序，排列自由度高
   - **必须全部完成**，缺少任何一项，无法执行某一步骤，导致事件图不连通
   - 简而言之：一组事件 **无强制顺序、允许任意交错或顺序执行**，但 **必须全部完成后才能进入下一事件**。它不要求事件同时开始，仅要求全部结束后的同步。
3. 并且我们希望设定更困难的生成式任务：根据给定的无序事件集，模型需要自己正确判断事件之间的关系，生成完整的事件图（json文件），这里先给出人工生成的示例数据：



### 二、样本构建样例

1. **标准结构：顺序+选择+循环**

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
    "script": [
      "0",
      "1",
      {
        "type": "select",
        "options": [
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

2. 同步汇合（AND-JOIN）（**这一条可以不看**）

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
    "script": [
      "0",
      {
        "type": "and_join",
        "script": ["1", "2", "3"]
      },
      "4",
      "5",
      "6",
      "7"
    ]
  }
}
```

3. **同步汇合（修正版**）

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
    "script": [
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

**同步汇合（AND-JOIN）**是弱化了**同时性**，并结合了**可交换（无序）**特点的一种“并行”结构：

- **分支之间无强依赖**，分支(branches)之间没有无明显的时间前后或因果关系，可同时执行，可以顺序执行，也可以执行分支A一段时间后，再执行分支B；简而言之，分支之间无序，排列自由度高
- **各分支必须全部完成**，缺少任何一个分支，无法执行某一步骤，导致事件图不连通
- **各个分支内的事件有逻辑顺序**，A1->A2表示分支A上的事件A1比同分支上的A2先发生

简而言之：多个分支（每个分支可包含多个时间） **无强制顺序、允许任意交错或顺序执行**，但 **必须全部完成后才能进入下一事件**。它不要求分支同时开始，仅要求全部结束后的同步。

4. **选择结构与嵌套**

```json
{
  "id":4,
  "scenario":"solve the dinner problem",
  "unordered_nodes":{
    "0":"visit the wet market",
    "1":"cook by oneself",
    "2":"open the food delivery app",
    "3":"order from a 30-minute guaranteed fast-food chain",
    "4":"choose a highly-rated yet remote internet-famous restaurant",
    "5":"wait for the takeaway",
    "6":"invite friends to go out for dinner",
    "7":"queue up at the internet-famous restaurant",
    "8":"solve the dinner problem"
  },
  "edges":[
    "0->1",
    "2->3",
    "2->4",
    "3->5",
    "4->5",
    "6->7",
    "1->8",
    "5->8",
    "7->8"
  ],
  "script_graph":
  {
    "type":"sequence",
    "script":
    [
      {
        "type":"select",
        "options":[
          {
            "type":"sequence",
            "script":["0","1"]
          },
          {
            "type":"sequence",
            "script":[
              "2",
              {
                "type":"select",
                "options":["3","4"]
              },
              "5"
            ]
          },
          {
            "type":"sequence",
            "script":["6","7"]
          }
        ]
      },
      "8"
    ]
  }
}
```

5. **循环结构与嵌套**

```json
{
  "id": 5,
  "scenario": "adjust coffee strength until perfect",
  "unordered_nodes": {
    "0": "grind coffee beans",
    "1": "brew a test cup",
    "2": "taste and evaluate strength",
    "3": "strength is perfect",
    "4": "add more ground coffee",
    "5": "extend brewing time",
    "6": "re-brew coffee",
    "7": "enjoy coffee"
  },
  "edges": [
    "0->1",
    "1->2",
    "2->3",
    "2->4",
    "2->5",
    "4->6",
    "5->6",
    "6->2",
    "3->7"
  ],
  "script_graph": {
    "type": "sequence",
    "script": [
      "0",
      "1",
      {
        "type": "loop",
        "entry": "2",
        "retry": [
          {
            "type": "select",
            "options": ["4", "5"]
          },
          "6",
          "continue"
        ],
        "exit": "3"
      },
      "7"
    ]
  }
}
```

6. **同步汇合结构与嵌套**

```json
{
  "id": 6,
  "scenario": "replicate the pasta at the restaurant",
  "unordered_nodes": {
    "0": "decide to replicate the pasta at the restaurant",
    "1": "turn on the stove",
    "2": "boil water for noodles",
    "3": "heat sauce in a pot",
    "4": "put noodles in the water",
    "5": "combine noodles and sauce",
    "6": "drain noodles over the sink",
    "7": "plate noodles and sauce together",
    "8": "choose red sauce",
    "9": "choose white sauce",
    "10": "replicate the pasta at the restaurant"
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
    "7->10",
    "3->8",
    "3->9",
    "8->5",
    "9->5"
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
            "3",
            {
              "type": "select",
              "options": [
                "8",
                "9"
              ]
            }
          ]
        }
      },
      "5",
      "7",
      "10"
    ]
  }
}
```



### 三、约束

1. 数据集结构具体说明

   - `id(int)`：序号
   - `scenario(string)`：具体场景，目的，**一般**可作为事件链的结束标志（不绝对）
   - `unordered_nodes(dictionary)`：组成事件链的**无序**事件单元
   - `edges(list)`：事件图的**所有**关联边（有向边）
   - `script_graph`：事件链/图的文字描述形式

2. **通用约束**

   - 完整性约束：图中任何结构（包括嵌套最深处的子结构）都必须有**对应的边和节点**
   - 单一入口/出口：原则上讲，每个结构都应该有对应且**唯一**的入口节点和出口节点，这样保证了事件不会发散
   - 连通性约束：不允许有悬挂的节点和边；对于任何嵌套子结构，控制流必须能从该结构的出口结点，**正确地连接到上层结构的下一个节点**
   - 复杂度约束：为保证可读性和避免过度复杂，应该**限制嵌套深度**

3. 事件图（**script_graph**）具体结构格式规范

   -  基本要求：

     - 每种结构使用`{}`括起来，需要标明`type`（**`branches_set`后的`{}`里不需要注明`type`**，这点需要着重注意），每种`type`有对应的结构**关键字**

     - `[]`中按照逻辑顺序**线性**罗列事件（**`selcect`**结构除外），使用`,`隔开；

     - `[]`可以嵌套其他结构，其格式要求同上

   - 顺序

     ```json
     {
       "type":"sequence",
       "script":[]
     }
     ```

   - 选择

     ```json
     {
       "type":"select",
       "options":[]
     }
     ```

   - 循环

     ```json
     {
       "type":"loop",
       "entry":"",
       "retry":[],
       "exit":""
     }
     ```

   - 同步汇合

     ```json
     {
       "type":"and_join",
       "branches_set":
       {
         "b1":[],
         "b2":[]
       }
     }
     ```

4. 嵌套规则

   - 顺序：可以包含任何结构，但要保证内部元素（结构）的**严格执行顺序**
   - 选择
     - 各选项可以包含任何结构，每个选项通常是一个`sequence`
     - **所有选型必须最终汇合到同一节点**
     - 选项间**互斥**，且原则上需要存在较大差异性（例如吃饭时选择用筷子、勺子则差异不明显）
     - **选型间无依赖边**，即 `edges` 中不能有跨选项的边（选项之间不连通）
   - 循环
     - 结构组成：`entry`：循环入口，为单一节点；`retry`：循环体，为子事件链，通常是一个`sequence`，可以包含任意子结构；`exit`：循环出口，为单一节点
     - 循环体逻辑上必须以 `entry` 节点开始；`retry` 链的最后一个元素必须是 **`"continue"`** ，且该序列执行完后必须能回到 `entry`；`exit` 节点必须**有边指向循环外部**的第一个节点，**且循环体内不应有其他路径能在不经过 `exit` 的情况下跳出循环**
     - 受限于能力，暂时无法实现**计数类循环**
     - 构建循环体时应该避免**无限循环**
   - 同步汇合
     - 各分支内部可以包含任何结构，每个分支采用 $b_i(i=1,2,\ldots)$命名，以 $b_i:[\ \ ]$ 呈现，通常是一个`sequence`；
     - **所有分支必须全部到达汇合点**，汇合点不出现在 `branches_set` 中，但图中必须有一个汇合节点，且每个分支的最后节点都有一条边指向它
     - **分支间无依赖边**，即 `edges` 中不能有跨分支的边
