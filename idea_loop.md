1.主要攻克选择和循环结构

2.选择结构，数据集构造方法和任务构建暂时参考*Choice-75*

3.循环结构，目前数据集结构和任务构建思路借鉴*Choice-75*，任务同样采用判别式：判断是否继续循环，可以对任务进行难度等级分级

4.循环结构数据集：目标（场景），步骤（编号0,1, ...），循环步骤（指出循环步骤编号），是否循环（给定一个条件或者说是情况，判断是否需要继续循环，0表示不需要，1表示需要，2表示情境与循环判断无关，无法影响决策），难度评级（推理出是否循环需要多少步，直接一步记为easy，两步为medium，三步为hard，若无影响为na）

5.示例：

```json
{
  "goal": "open the electronic lock",
  "steps": [
    "go home",
    "activate the electronic lock",
    "enter the password",
    "open the door"
  ],
  "loop_idx": 2,
  "loop_step": "enter the password",
  "situation": [
    [
      "incorrect password",
      1,
      "easy"
    ],
    [
      "the lock is brand new",
      2,
      "na"
    ],
    [
      "the door opened from the inside",
      0,
      "easy"
    ],
    [
      "too many incorrect attempts,system shows locked screen",
      0,
      "medium"
    ],
    [
      "forget the password",
      0,
      "medium"
    ]
  ]
}
```

说明：

密码错误→继续循环（1步，easy）；

锁是全新的→无关条件（na）

门从里面打开→退出循环（1步，easy）

输入次数过多，系统屏幕锁定→锁屏，无法输入密码→退出循环；(2步，medium)

忘记密码→避免门锁定，放弃输密码→退出循环；(2步，medium)

```json
{
    "goal": "charge the phone to full battery",
    "steps": [
        "find the charger and cable",
        "plug the charger into the power outlet",
        "connect the cable to the phone's charging port",
        "wait for the battery to reach 100%"
    ],
    "loop_idx": 3,
    "loop_step": "wait for the battery to reach 100%",
    "situation": [
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
            "the charging icon disappears and the phone feels warm",
            0,
            "hard"
        ],
        [
            "the phone displays 'optimized battery charging'" ,
            0,
            "hard"
        ]
    ]
}
```

说明：

电池显示85%→继续循环（easy）

手机是全新的，刚从盒子拿出来→无关条件（na）

锁屏上显示电池100%→退出循环（easy）

手机显示“充电暂停：温度过高”→充电暂停，无法达到100%→退出循环

充电图标消失，手机感觉发热→手机启动过热保护，停止充电→手机电量无法达到100%→退出循环

手机显示“优化电池充电”→手机电量在80%左右以保护电池→手机继续充电但电量无法达到100%→退出循环



模仿*choice-75*中的用户画像（user_profile），同样可以添加描述用户条件、状态等的信息（其中包含噪声，即大量无用信息），让模型判断是否需要循环（**目前还没确定是否添加，引入噪声来检测模型的能力是不错的想法，但原文注重选择结构，用户特征会影响选择，但对判断某件事是否会继续循环不一定作用显著。目前尝试构建的数据，其用户画像过于刻意，不够令人满意。后续会优化提示词或引入其他更合适想法**）

```json
{
  "goal": "boil water for pasta",
  "steps": [
    "fill a pot with water",
    "place the pot on the stove",
    "turn the burner to high heat",
    "wait for the water to come to a rolling boil",
    "add salt and pasta to the boiling water"
  ],
  "loop_idx": 3,
  "loop_step": "wait for the water to come to a rolling boil",
  "situation": [
    [
      "Name: Casey\nPreferences: Very anxious about kitchen safety; always double-checks appliances.\nInterests: True crime podcasts, home organization, baking.\nFinancial Situation: Budget-conscious, saves diligently.\nOccupation: Administrative assistant.\nHobbies: Knitting, jigsaw puzzles, bird watching.\nGender: Non-binary\nLife-style: Prefers to stay in the kitchen while cooking to monitor everything. Has a fear of fires.",
      1,
      "medium"
    ],
    [
      "Name: Riley\nPreferences: Multitasks constantly; hates standing still.\nInterests: Productivity hacks, listening to audiobooks, cleaning.\nFinancial Situation: Upper-middle class.\nOccupation: Marketing manager.\nHobbies: Running marathons, volunteering at animal shelters.\nGender: Female\nLife-style: Always on the go, uses cooking time to do other chores around the house. Often forgets about things on the stove.",
      0,
      "hard"
    ],
    [
      "Name: Taylor\nPreferences: Indifferent to cooking methods; just follows the recipe exactly.\nInterests: Mathematics, chess, classical music.\nFinancial Situation: Comfortable.\nOccupation: Data analyst.\nHobbies: Playing piano, solving Rubik's cubes.\nGender: Male\nLife-style: Very methodical and precise. Sets a timer for everything.",
      0,
      "na"
    ]
  ]
}
```



6.获取数据集

利用proscript，只是一个包含场景"scenario"（目标）和事件"events"（步骤）的数据集，其中给出了完成目标的正确步骤"flatten_output_for_script_generation"，我们希望删去其中不必要的信息，保留目标和正确步骤，并在此基础上合理选择引入循环结构（verb_phrase和user_profile）

proscript示例：

```json
[
  {
    "scenario": "ride a train",
    "events": {
      "0": "walk to train to ride",
      "1": "ask to buy ticket",
      "2": "walk to ticket booth",
      "3": "pay for ticket",
      "4": "walk up to booth",
      "5": "NONE",
      "6": "ride a train"
    },
    "context": "NONE",
    "minutes": 13,
    "events_minutes": {
      "0": 5,
      "1": 1,
      "2": 3,
      "3": 2,
      "4": 1
    },
    "flatten_input_for_edge_prediction": "step0: walk to train to ride; step1: ask to buy ticket; step2: walk to ticket booth; step3: pay for ticket; step4: walk up to booth; step5: decided to ride a train; step6: ride a train",
    "flatten_input_for_script_generation": "You want to ride a train. How can you do this in 7 steps?",
    "flatten_output_for_edge_prediction": "step2 -> step4; step4 -> step1; step1 -> step3; step3 -> step0; step0 -> step6; step5 -> step2",
    "flatten_output_for_script_generation": "step0: decided to ride a train; step1: walk to ticket booth; step2: walk up to booth; step3: ask to buy ticket; step4: pay for ticket; step5: walk to train to ride; step6: ride a train; step0 -> step1; step1 -> step2; step2 -> step3; step3 -> step4; step4 -> step5; step5 -> step6",
    "gold_edges_for_prediction": [
      "0->6",
      "1->3",
      "2->4",
      "3->0",
      "4->1",
      "5->2"
    ]
  }
]
```









