# Piper 任务库人工收紧标准

用户已批准将下表中 7 个任务的允许误差收紧到 0.02 m。修订直接落在 `AA1/auto_adapter/task_libraries/piper/1.0.0/catalog.json` 的 `tasks[].scoring[].threshold`，不另建人工要求覆盖文件。这是人工确定的任务标准，不是物理校准结论。

## 修订前的 20 项标准

| task_id | 测量对象／metric | 原阈值 |
|---|---|---|
| `mw_reach_target` | `end_effector_target_distance` | <= 0.05 m |
| `mw_push_to_goal` | `object_goal_distance` | <= 0.05 m |
| `mw_pick_place` | `object_goal_distance` | <= 0.07 m |
| `mw_pick_place_wall` | `object_goal_distance` | <= 0.07 m |
| `mw_push_wall` | `object_goal_distance` | <= 0.07 m |
| `mw_sweep_into_goal` | `object_goal_distance` | <= 0.05 m |
| `mw_drawer_open` | `drawer_handle_target_distance` | <= 0.03 m |
| `mw_drawer_close` | `drawer_target_distance` | <= 0.065 m |
| `mw_button_press` | `button_target_distance` | <= 0.02 m |
| `mw_button_press_topdown` | `button_target_distance` | <= 0.024 m |
| `mw_handle_press` | `handle_target_distance` | <= 0.02 m |
| `mw_handle_pull` | `handle_target_distance` | <= 0.05 m |
| `mw_door_open` | `door_x_axis_error` | <= 0.08 m |
| `mw_door_close` | `door_target_distance` | <= 0.08 m |
| `mw_faucet_open` | `faucet_target_distance` | <= 0.07 m |
| `mw_dial_turn` | `dial_target_distance` | <= 0.07 m |
| `mw_lever_pull` | `lever_angle_error` | <= 0.1308996939 rad |
| `mw_peg_insertion_side` | `peg_target_distance` | <= 0.07 m |
| `mw_bin_picking` | `object_goal_distance` | <= 0.05 m |
| `mw_pick_out_of_hole` | `object_goal_distance` | <= 0.07 m |

只有 reach task 直接测量机器人末端。其余 19 个 task 测量对象或 fixture 的状态；数值相同不代表可以直接作为末端控制误差。

## 已批准的 7 项修订

| task_id | 原容差 | 人工调整后的容差 |
|---|---|---|
| `mw_reach_target` | 0.05 m | 0.02 m |
| `mw_push_to_goal` | 0.05 m | 0.02 m |
| `mw_sweep_into_goal` | 0.05 m | 0.02 m |
| `mw_drawer_open` | 0.03 m | 0.02 m |
| `mw_button_press_topdown` | 0.024 m | 0.02 m |
| `mw_handle_pull` | 0.05 m | 0.02 m |
| `mw_bin_picking` | 0.05 m | 0.02 m |

`scoring[].threshold` 保存本地生效值。相应任务和评分来源条目的 `adaptation` 是给 DESIGN 阅读的适配说明，注明原来源值以及本地人工调整后的 0.02 m。`scoring[].source_refs[].specific_reference` 中记录的原来源事实保持原文，`sources.json` 保持不变；不把 2 cm 声称为来源原标准。测量对象、单位、比较符、时间语义和调用参数不变。

已经是 2 cm 的 `mw_button_press`、`mw_handle_press` 保持不变；角度标准 `mw_lever_pull` 也保持不变。

## 本批未修改的较大距离容差

| 原容差 | task_id |
|---|---|
| 6.5 cm | `mw_drawer_close` |
| 7 cm | `mw_pick_place`、`mw_pick_place_wall`、`mw_push_wall`、`mw_faucet_open`、`mw_dial_turn`、`mw_peg_insertion_side`、`mw_pick_out_of_hole` |
| 8 cm | `mw_door_open`、`mw_door_close` |

## 本轮接触 case 为什么需要连场景一起审阅

固定方块中心 z=0.28 m、半高 0.05 m，顶面 z=0.33 m；请求的末端目标 z=0.30 m 位于方块内部。实际末端停在 z≈0.34225 m，误差约42.25 mm，在当前50 mm容差下通过。若另将这个 capability 的标准收紧到20 mm，同一结果将失败；这不应被自动归因于 driver，而要先检查末端/指尖几何与接触目标是否相容。

峰值接触力≥0.5 N只说明出现过接触力。若人工要求持续接触，需要明确接触区间和持续条件；增大峰值阈值不能替代持续性测量。

## 对 DESIGN 和已有记录的影响

后续 DESIGN 通过原有 `read_file` 读取任务库，就会看到这 7 项人工收紧后的评分值。已有 `capability_design.json`、导出的 criteria、场景和运行结果不会自动被修改；本批也没有重新调用模型或执行 MuJoCo。

旧诊断保留当时的任务输入和通过标准，不能作为收紧标准后的通过证据。新生成的能力标准还需核对是否正确回应任务需求，尤其不能把物体误差直接照搬成机器人末端误差。
