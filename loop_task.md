### 一、总体思路与任务

1. 针对循环结构构建判别式任务：根据描述判断事件是否会继续循环
2. 从 `proscript` 数据集中选取合适数据，加以改造



### 二、数据集

1. 示例

   ```json
   {
       "id":1,
       "sceniro": "charge the phone to full battery",
       "steps": [
           "find the charger and cable",
           "plug the charger into the power outlet",
           "connect the cable to the phone's charging port",
           "wait for the battery to reach 100%"
       ],
       "loop_idx": 3,
       "loop_step": "wait for the battery to reach 100%",
       "descriptions": [
           [
               "battery is at 85%",
               1,
               "easy"
           ],
           [
               "the phone is brand new out of the box",
               2,
               "na"
           ],
           [
               "battery shows 100% on the lock screen",
               0,
               "easy"
           ],
           [
               "the phone displays 'Charging paused: Temperature too high'",
               0,
               "medium"
           ],
           [
               "the phone displays 'optimized battery charging'" ,
               0,
               "hard"
           ]
       ]
   }
   ```

2. 具体字段说明

   - `id`：数据编号/序号
   - `sceniro`：事件链目标
   - `steps`：具体步骤
   - `loop_idx` 和 `loop_step` ：`steps` 中循环步骤的编号和具体内容
   - `descriptions`：包含三个字段：
     - 问题（状态描述）：对事物或事件的具体描述，用于判断事件 `loop_step` 是否会继续循环
     - 答案：$0$ 表示退出循环，$1$ 表示继续循环，$2$ 表示状态描述与循环判断无关
     - 难度：`easy`, `medium`, `hard`, `na`，其中 `na` 用于标注状态描述与循环判断无关的问题

3. 构建提示词

   - **困难等级**：原来使用 "推理步数" 作为难度评级的准则，但是**逻辑链越长并不代表推理越困难，过于单一**。因此我们需要**额外**考虑其他量化标准，目前除了使用**人工标准的一致性**，暂无想到其他更好方法

   - 在同一上下文中，用于提示生成 `continue/stop` 状态描述的提示词：

     ```txt
     Prompt 1:
     The scenario is "{scenario}".
     One of the steps towards that is:{steps}.
     Step {loop_idx} "{loop_step}" involves a loop.
     Because {{please_insert}}, the loop needs to continue.
     
     (Explanation: Do not simply state the loop's goal is not achieved. Instead, give a concise, concrete description can infer that the loop has not ended. It should be a factual clue, not "the goal is not met".)
     Only output the text that should replace {{please_insert}}.
     
     Prompt 2:
     The scenario is "{scenario}".
     One of the steps towards that is:{steps}.
     Step {loop_idx} "{loop_step}" involves a loop.
     Because {{please_insert}}, {reason_one}.
     
     (Explanation: Now give a different description({{please_insert}}). This description must be less immediately obvious, requiring a small logical step to link it to the loop continuing. It is a less direct observation that still independently implies the loop should continue. Try to avoid overly professional knowledge.)
     Only output the text that should replace {{please_insert}}.
     
     Prompt 3:
     The scenario is "{scenario}".
     One of the steps towards that is:{steps}.
     Step {loop_idx} "{loop_step}" involves a loop.
     Because {{please_insert}}, {reason_two}.
     
     (Explanation: Give another different and subtle description({{please_insert}}) that still independently implies "continue", only requiring the exclusion of some distractions or the combination of clues to make a deduction based on common sense. Avoid overly long descriptions and excessive explanations. Try to avoid specialized knowledge. That is, the reasoning is longer and complex, not the knowledge more difficult, professional, or obscure.)
     Only output the text that should replace {{please_insert}}.
     ```

     显然，根据**跳数**，可以规定 `Prompt 1`，`Prompt 2`，`Prompt 3` 导出的问题难度分别为 `easy`，`medium`，`hard`

   - 用于提示生成**无关**状态描述的提示词：

     ```txt
     The scenario is "{scenario}".
     One of the steps towards that is:{steps}.
     Step {loop_idx} "{loop_step}" involves a loop.
     
     Give two pieces of contextual information that are related to the current scene but have absolutely no causal effect on the loop decision. The information should feel natural, but must not help decide whether the loop continues, stops or has already stopped.
     
     Critical: In the current scenario, the information must not imply that the loop is ongoing, about to end, or has already ended. For instance, if the loop is "wait for computer to turn on", you must not mention things visible only after the computer is on.
     
     Return a JSON array of exactly two strings.
     Only output the JSON array, nothing else.
     ```

4. 引入噪声

   - 在利用提示词引导出 `easy`，`medium`，`hard`，`na` 各种状态描述后，在其基础上添加噪声，难度继承，以测试模型的抗噪能力

   - 核心要求：

     - 改写后的状态描述必须与原描述得出完全相同的循环判定
     - 难度直接继承，即输出时保留原难度，不做调整
     - 噪声必须与 `sceniro`、`loop_step` 以及原状态描述相关，但添加的文本对循环判定无因果影响

   - 提示词：

     ```txt
     Your task is to augment the existing data by adding reasonable noise to each state description (the first field in `descriptions`).
     
     ## Data Template
     {
       "id": number,
       "sceniro": "scenario description",
       "steps": ["step 1", "step 2", ...],
       "loop_idx": index of the loop step (note: indexing may start from 0),
       "loop_step": "content of the loop step",
       "descriptions": [
         ["state description text", answer (0/1/2), difficulty ("easy"/"medium"/"hard"/"na")],
         ...
       ]
     }
     
     ## Field Explanation
     - Answer: 0 = exit loop, 1 = continue loop, 2 = irrelevant to loop decision
     - Difficulty: easy, medium, hard, na (na is used only when answer = 2)
     
     ## Noise Addition Requirements
     1. **Answer unchanged**: The rewritten state description must lead to exactly the same loop decision (0/1/2) as the original.
     2. **Difficulty directly inherited**: Keep the original difficulty level; do not adjust it.
     3. **Source of noise**: The added text must be related to `sceniro`, `loop_step`, and the original state description, but must have no causal impact on the loop decision. You may add background details, environmental information, user states, temporal modifiers, or other distracting but non-causal content.
     4. **Semantic proximity**: Preserve the core facts that determine whether the loop should continue/stop or are irrelevant. For answer=2 (irrelevant) descriptions, keep the original meaning as much as possible.
     
     
     ## Examples of Allowed Noise (using the "charge to 100%" scenario)
     - Original: `["battery is at 85%", 1, "easy"]`
       Allowed noise: `["according to the phone, the battery level is showing 85% and the room temperature is normal", 1, "easy"]` 
     - Original: `["battery shows 100% on the lock screen", 0, "easy"]`
       Allowed noise: `["the lock screen displays a full battery icon of 100% and it's early morning", 0, "easy"]` 
     - Original: `["the phone displays 'optimized battery charging'", 0, "hard"]`
       Allowed noise: `["the phone indicates 'optimized battery charging' is active with a small leaf icon", 0, "hard"]` 
     
     ## Output Format
     Directly output the complete JSON text with the same structure as the input. Only modify the first field of each `descriptions` entry (the state description text); keep the answer and difficulty unchanged.
     
     Input data:
     {paste the specific JSON data to be augmented here}
     ```



### 三、思路

1. `llm_fixed_dev.json` 是之前做事件图生成任务时，利用大模型对原始边和事件图进行逻辑修正得到的结果，其事件链的逻辑正确性高于 `ProScript` ，故这里优先使用。其结构如下：

   ```JSON
   {
     "id": 1,
     "scenario": "ride a train",
     "unordered_nodes": {
       "0": "walk to train to ride",
       "1": "ask to buy ticket",
       "2": "walk to ticket booth",
       "3": "pay for ticket",
       "4": "walk up to booth",
       "5": "ride a train"
     },
     "edges": [
       "0->5",
       "1->3",
       "2->4",
       "3->0",
       "4->1"
     ],
     "script_graph": {
       "type": "sequence",
       "script": [
         "2",
         "4",
         "1",
         "3",
         "0",
         "5"
       ]
     }
   }
   ```
2. `filter_dev.py` 说明：我们筛选数据集中 `script_graph` 仅包含 `sequence` 一种类型的数据（排除 `and_join` 这种稍显复杂，同时无法用单一事件链表示的结构），对 `id` 进行重新编号，保留 `scenario`，删除`unordered_nodes`，`edges`，`script_graph`，新增 `steps` 字段，按照原数据中的 `script_graph ` 中的顺序，对原 `unordered_nodes` 中的事件进行排序，得到 `filter_dev.json`
3. `filter_loop_dev.py` 说明：利用大模型筛选出 `filter_dev.json` 数据集中暗含循环语义的数据（例如，包含 `repeat`，`until` ，`wait` 等关键字），得到 `filter_loop_dev.json` （180条）
4. `filter_loop_quality_dev.py` 说明：利用大模型筛选出 `filter_loop_dev.json` 中更适合后续任务的数据，以下是符合条件的数据的特征：

   - 循环条件具有**发展性**，例如：水加热 → 小气泡 → 大气泡 → 剧烈沸腾
   - 循环设计**重复某项动作，并有可检查的中间结果**，例如搅拌至顺滑，调整调味并再次品尝
   - 循环的状态可以从**不同类型的的观察**中推断（视觉、听觉、触觉等）

   得到 `filter_loop_quality_dev.json`
5. `prompt_generate.py`说明：利用大模型按指定范式生成规范数据 `generated_backup.json` ：

   - continue/stop：在同一上下文中，依次读取如下提示词，对应生成 `easy`，`medium`，`hard` 数据

     - ```
       The scenario is "{scenario}".
       One of the steps towards that is:{steps}.
       Step {loop_idx} "{loop_step}" involves a loop.
       Because {{please_insert}}, the loop needs to continue.
       
       (Explanation: Do not simply state the loop's goal is not achieved. Instead, give a concise, concrete description can infer that the loop has not ended. It should be a factual clue, not "the goal is not met".)
       Only output the text that should replace {{please_insert}}.
       ```
     - ```txt
       The scenario is "{scenario}".
       One of the steps towards that is:{steps}.
       Step {loop_idx} "{loop_step}" involves a loop.
       Because {{please_insert}}, {reason_one}.
       
       (Explanation: Now give a different description({{please_insert}}). This description must be less immediately obvious, requiring a small logical step to link it to the loop continuing. It is a less direct observation that still independently implies the loop should continue. Try to avoid overly professional knowledge.)
       Only output the text that should replace {{please_insert}}.
       ```
     - ```txt
       The scenario is "{scenario}".
       One of the steps towards that is:{steps}.
       Step {loop_idx} "{loop_step}" involves a loop.
       Because {{please_insert}}, {reason_two}.
       
       (Explanation: Give another different and subtle description({{please_insert}}) that still independently implies "continue", only requiring the exclusion of some distractions or the combination of clues to make a deduction based on common sense. Avoid overly long descriptions and excessive explanations. Try to avoid specialized knowledge. That is, the reasoning is longer and complex, not the knowledge more difficult, professional, or obscure.)
       Only output the text that should replace {{please_insert}}.
       ```
   - na：

     ```txt
     The scenario is "{scenario}".
     One of the steps towards that is:{steps}.
     Step {loop_idx} "{loop_step}" involves a loop.
     
     Give two pieces of contextual information that are related to the current scene but have absolutely no causal effect on the loop decision. The information should feel natural, but must not help decide whether the loop continues, stops or has already stopped.
     
     Critical: In the current scenario, the information must not imply that the loop is ongoing, about to end, or has already ended. For instance, if the loop is "wait for computer to turn on", you must not mention things visible only after the computer is on.
     
     Return a JSON array of exactly two strings.
     Only output the JSON array, nothing else.
     ```
6. `loop_task_judge` ：隐藏答案和难度，让大模型判断答案，统计各难度正确率和混淆矩阵
7. `loop_task_noise` ：生成噪声，检验模型抗噪能力
