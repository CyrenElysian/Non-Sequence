**针对循环结构构建判别式任务：根据描述判断事件是否会继续循环**

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