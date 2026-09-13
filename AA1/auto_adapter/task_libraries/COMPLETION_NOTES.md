# 新补任务库：逐项审阅表

本批由 Astra Ultra 实施，补齐 A1、ANYmal-C、H1、Skydio X2 各20项任务。它们是 AA1 DESIGN 的公开需求输入，不是已实现的能力、私有验收套件、正式实验协议或成功证据。

**下表全部评分阈值、窗口、场景尺寸与事件容差均为 local proposed / uncalibrated（本地提议、未校准）**。一手来源支持应用/操作家族，不为这些新数字背书。机器人结构、实际碰撞几何与执行器类型取自本地 MJCF，不属于拟定阈值。没有从候选运行结果倒推标准，也没有保留或宣称达到原论文的回报分数。

评分条件应同时满足。`completed_operation == 1` 表示该项 `metric_definition` 中的全部有序事件成立；不是“调用成功”标志。计数与接触条款需要完整物理轨迹。表内列出全部 scoring 条款；更精确的坐标定义、场景假设、调用参数和来源定位见对应 catalog.json。所有无单位的角度事件均在原条款中明确为度或弧度。

四份 stock scene.xml 都只有机器人与平面。楼梯、地形、弹簧垫、活动板、物体、建筑物、参考轨迹及事件仅是未来任务场景的需求，本批没有创建这些场景或宣称其可运行。全部任务都保留真实机器人形态与执行器接口。

## 模型与旧配置边界

- A1：自由躯干、12个位置执行器；无手臂。足碰撞体没有名字，需要按真实模型树的 `class=foot` 绑定。
- ANYmal-C：自由底盘、12个位置执行器；没有可动巡检云台、机械臂或充电装置。当前 ANYbotics 产品页仅提供应用背景，不表示旧C版MJCF具有当前产品功能。
- H1：自由骨盆、19个力矩执行器；每臂4关节、无腕与手指。到达/推物使用真实前臂末端碰撞几何。
- X2：一个自由刚体、4个非负site推力执行器；有IMU，无GPS、视觉导航、云台关节、电池模型或产品飞控软件。`track` 是外部观察相机。拍摄/扫描仅转成飞行与几何观察要求，不证明成像或重建质量。
- G1/Barkour已有各20项公开任务，实际模型已迁入 `AA1/assets/mjcf/` 并绑定任务索引和固定 DEMO 配置。G1 使用 from-scratch；Barkour 使用四足 skeleton，均可通过显式 MJCF 的 orchestrator 入口调用。本次只完成本地模型接入，没有运行模型生成。`piper_push`/`franka_push`直接包含同一真实机器人，可复用对应任务输入；这仍不等于其旧场景能执行所有任务。
- 裸 `ur5e` 没有Robotiq夹爪，不能使用UR5e+2F85整包。旧 `so101` 双jaw无接触并依赖inactive weld；`so101_push` 仅fingertip有接触，不能据此绑定Menagerie SO101真实夹爪整包。上述三个旧配置在本批保留未绑定，不扩建新子集。

## 来源读取方式

每个评分条款的 `source_refs.specific_reference` 直接包含公开URL与章节/函数定位，DESIGN只读catalog时也能追溯操作来源。sources.json记录组织、题名与范围。引用的live页面按2026-09-13访问记录，不编造commit、行号、版本或本地模型与上游revision等同关系。

## Unitree A1（20项，全部 proposed / uncalibrated）

[完整任务](unitree_a1/1.0.0/catalog.json) · [一手来源记录](unitree_a1/1.0.0/sources.json)

| ID / 任务 | 具体操作与场景提议 | 指标与阈值（全部同时满足） | 期限 |
| --- | --- | --- | --- |
| A1-T01 点目标到达并停稳<br>Point-goal arrival and stop | 2 m目标；末2 s到达并停稳。 | `terminal_goal_planar_error` <= 0.15 m（末2s）<br>`terminal_planar_speed` <= 0.08 m/s（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T02 杂乱房间出口导航<br>Cluttered-room exit | 依次绕家具转弯，穿过出口；不得碰墙/家具。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T03 碰撞后退让并绕行<br>Recover a route after barrier contact | 先实际触障，再退≥0.15 m，随后绕行抵达。 | `completed_operation` == 1 binary<br>`terminal_goal_planar_error` <= 0.15 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T04 礼让横穿障碍<br>Yield to a crossing obstacle | t=4–8 s停在线前；障碍离开后继续。 | `completed_operation` == 1 binary<br>`terminal_goal_planar_error` <= 0.15 m（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T05 不稳路段主动降速<br>Slow down for uncertain footing | 标注1 m区段内限速；完整通过后继续。 | `completed_operation` == 1 binary<br>`section_speed_limit` <= 0.15 m/s<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T06 碎石块地面通过<br>Rock-field crossing | 通过2 m不平块地；躯干不接触地形。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T07 低摩擦地面转换<br>Low-friction transition | 穿过1 m摩擦0.3区段，回到摩擦0.8地面。 | `completed_operation` == 1 binary<br>`maximum_cross_track_error` <= 0.3 m（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T08 活动木板通过<br>Moving-plank crossing | 两块被动限角±5°木板，依次接触后离开。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T09 弹性支撑垫通过<br>Compliant-pad crossing | 四个被动弹簧垫，至少两垫受载下沉后离开。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T10 楼梯通行<br>Stair-flight traversal | 3级台阶：级高0.05 m、踏面0.30 m。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T11 孤立平台上下通行<br>Isolated-platform mount and dismount | 0.10 m高平台：四足上台后再越过远端。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T12 斜坡进入高台<br>Inclined access ramp | 8°、1.2 m长斜坡进入高台。 | `completed_operation` == 1 binary<br>`maximum_cross_track_error` <= 0.25 m<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T13 离散踏石通过<br>Stepping-stone crossing | 两排共6块踏石；禁止接触间隙下方地面。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T14 跨沟腾空跳跃<br>Gap leap | 0.15 m沟；跨越时同时腾空≥0.02 s。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T15 受限通道攀上高块<br>Climb a block without a run-around | 0.16 m高块；允许块体辅助接触，禁绕侧墙。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T16 低顶通道通过<br>Low-overhead passage | 0.28 m净高、0.6 m长通道；不碰顶。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T17 窄缝身体重构<br>Narrow-slit body reconfiguration | 0.34 m宽直立开口；全身通过且不碰壁。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T18 地形凹坑脱困<br>Escape a terrain depression | 盆地半径1.5 m；末态在边缘之外。 | `terminal_distance_from_basin_center` >= 1.7 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T19 碰撞推球入收集区<br>Push a ball to a collection zone | 半径0.10 m、质量0.2 kg球；必须实际碰球。 | `completed_operation` == 1 binary<br>`terminal_ball_goal_error` <= 0.2 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| A1-T20 侧卧后恢复站立<br>Recover from a side-resting pose | 初始侧卧90°；末2 s四足支撑且躯干离地。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 15 deg（末2s）<br>`terminal_trunk_ground_clearance` >= 0.12 m（末2s） | 30 s |

## ANYmal-C（20项，全部 proposed / uncalibrated）

[完整任务](anybotics_anymal_c/1.0.0/catalog.json) · [一手来源记录](anybotics_anymal_c/1.0.0/sources.json)

| ID / 任务 | 具体操作与场景提议 | 指标与阈值（全部同时满足） | 期限 |
| --- | --- | --- | --- |
| ANYC-T01 依次巡检并返回<br>Inspection round with station dwell | 3站按序，每站在0.20 m圈内连续停2 s；最后返回。 | `ordered_station_visits` == 3 station<br>`terminal_goal_planar_error` <= 0.2 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T02 对准设备的静止观察<br>Stationary asset-facing observation | 身体+x朝向设备；仅位置与朝向，不识别仪表。 | `terminal_goal_planar_error` <= 0.15 m（末2s）<br>`terminal_asset_bearing_error` <= 10 deg（末2s）<br>`terminal_base_speed` <= 0.05 m/s（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T03 巡检区域覆盖<br>Area-coverage inspection route | 4条巡检航道式步行路线；不测气体。 | `ordered_lane_coverage` == 1 fraction<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T04 停靠位姿接近<br>Docking-pose approach | 0.10 m位置/8°朝向；仅预接触位姿，不充电。 | `terminal_goal_planar_error` <= 0.1 m（末2s）<br>`terminal_dock_heading_error` <= 8 deg（末2s）<br>`terminal_base_speed` <= 0.05 m/s（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T05 阻断通道绕行<br>Blocked-aisle inspection detour | 绕被箱体堵塞的3 m路线，不碰箱体或墙。 | `terminal_goal_planar_error` <= 0.2 m（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T06 作业区前受令停车<br>Commanded stop before a work zone | 初速0.3 m/s；5 s内停住，禁止越作业区。 | `stopping_distance` <= 0.4 m<br>`work_zone_entries` == 0 entry（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 5 s |
| ANYC-T07 狭窄设备舱进退<br>Confined equipment-bay access | 进1.5 m深盲端舱再退回；全程转向≤30°。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T08 前足触探<br>Foot contact probe | 左前足离地触目标0.5 s；其余三足支撑，再恢复。 | `completed_operation` == 1 binary<br>`probe_peak_normal_force` <= 120 N<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T09 不平服务道路通过<br>Uneven service-road crossing | 固定块高0.03–0.10 m；禁止底盘触地。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T10 湿滑路面摩擦转换<br>Wet-floor friction transition | 摩擦0.3/0.8转换；不模拟水体或IP等级。 | `completed_operation` == 1 binary<br>`maximum_cross_track_error` <= 0.35 m（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T11 不稳支撑板通过<br>Unstable support crossing | 被动摇板加载转动≥1°，随后四足到远端。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T12 下沉弹性地面通过<br>Yielding-floor crossing | 4个弹簧垫、行程0.025 m；至少2个受载后离开。 | `completed_operation` == 1 binary<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T13 镂空栅格楼梯<br>Grated stair access | 3级0.10 m台阶；0.04 m栅条与0.02 m槽。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T14 斜坡进入设备平台<br>Ramp to raised equipment deck | 10°、1.5 m长斜坡到设备平台。 | `completed_operation` == 1 binary<br>`maximum_cross_track_error` <= 0.3 m<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T15 稀疏踏石通过<br>Sparse support-stone crossing | 6块0.40 m踏石、0.10 m缝隙；禁碰下方地面。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T16 巡检路线跨沟<br>Inspection-route gap crossing | 0.20 m沟；同时腾空≥0.02 s后四足落远岸。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T17 跨越阻路高台<br>Climb over a route-blocking platform | 0.25 m高台；四足上台再越过远端。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T18 管线下方低姿通过<br>Under-pipe inspection access | 0.55 m净高、0.8 m长顶板下穿行。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T19 连续多障碍通行<br>Multi-obstacle inspection approach | 依次高块→低顶→沟；不允许中途重置。 | `ordered_obstacles_completed` == 3 obstacle<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |
| ANYC-T20 浅盆地脱困<br>Basin egress | 盆地半径1.8 m；末态越出盆地。 | `terminal_distance_from_basin_center` >= 2.1 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s） | 30 s |

## Unitree H1（20项，全部 proposed / uncalibrated）

[完整任务](h1/1.0.0/catalog.json) · [一手来源记录](h1/1.0.0/sources.json)

| ID / 任务 | 具体操作与场景提议 | 指标与阈值（全部同时满足） | 期限 |
| --- | --- | --- | --- |
| H1-T01 交替足支撑行走<br>Sustained alternating-foot walk | 5–15 s跟踪0.4 m/s；每足≥3次摆动/落足，至少一足支撑。 | `walking_speed_rmse` <= 0.15 m/s<br>`completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T02 站立等待<br>Standing observation hold | 30 s双足持续支撑；禁止非足部接地。 | `standing_planar_drift` <= 0.08 m（全程）<br>`completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T03 含腾空阶段的跑步<br>Running gait with flight | 5–12 s跟踪1.2 m/s；≥3次相隔的≥0.02 s腾空。 | `running_speed_rmse` <= 0.3 m/s<br>`running_flight_intervals` >= 3 interval<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T04 前臂末端三维到达<br>Distal forearm reach | 真实left_elbow_link局部[0.28,0,-0.015]点；不抓取。 | `forearm_target_error` <= 0.06 m（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T05 低跨栏通过<br>Low-hurdle crossing | 0.10 m高栏；双足越过且不碰栏/侧墙。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T06 低顶隧道爬行<br>Low-tunnel crawl | 1.0 m净高隧道；可用实际膝/前臂等支撑，末态站立。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T07 多转弯走廊导航<br>Multi-turn corridor navigation | 两处直角弯检查点按序通过；不碰走廊壁。 | `completed_operation` == 1 binary<br>`terminal_goal_planar_error` <= 0.2 m（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T08 落座并获得座面支撑<br>Transfer into a supported seat | 0.55 m高凳；髋部真实接触座面且双足接地。 | `completed_operation` == 1 binary<br>`seated_pelvis_planar_error` <= 0.12 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`seated_planar_speed` <= 0.05 m/s（末2s） | 30 s |
| H1-T09 活动平衡板站立<br>Balance on a tilting board | 被动双轴±5°板；禁止接触周围地面/支撑机构。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T10 楼梯进入平台<br>Stair access to a landing | 3级0.08 m楼梯；双足上平台。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T11 倾斜步道通过<br>Inclined walkway traversal | 8°斜坡；双足到高处平台。 | `completed_operation` == 1 binary<br>`maximum_cross_track_error` <= 0.25 m<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T12 绕柱蛇形行走<br>Pole slalom | 3柱按指定交替侧依次通过；禁止碰柱。 | `correct_pole_passes` == 3 pole<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T13 前臂推桌上物体<br>Forearm tabletop push | 0.15 m、0.5 kg方块；需真实前臂接触且物体留桌。 | `completed_operation` == 1 binary<br>`object_goal_planar_error` <= 0.08 m（末2s）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T14 选择并触碰目标按钮<br>Select and touch a target button | 3个固定接触靶中选1个；不得碰另外2个。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T15 环形巡检行走<br>Circular inspection walk | 半径1.5 m一整圈；反向运动扣减进度。 | `signed_circuit_progress` >= 6.283185307179586 rad<br>`maximum_radius_error` <= 0.25 m（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T16 避开禁落足区域<br>Navigate around excluded ground | 2个半径0.30 m禁区；任何落足接触点不得入区。 | `terminal_goal_planar_error` <= 0.2 m（末2s）<br>`forbidden_foot_placements` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T17 动态障碍穿行<br>Cross a route with moving obstacles | 两个按已知圆轨迹运动的箱体；不碰撞且抵达。 | `terminal_goal_planar_error` <= 0.2 m（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T18 跨沟跳跃落地<br>Gap leap and landing | 0.15 m沟；双足同时腾空≥0.02 s，再落远岸。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T19 跳上升高平台<br>Jump onto an elevated platform | 0.12 m高台；首次接触前腾空≥0.02 s，末态双足在顶面。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |
| H1-T20 不平室外路线通过<br>Uneven outdoor-route traversal | 2 m区域，固定高度0.02–0.06 m；禁头/躯干触地。 | `completed_operation` == 1 binary<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 25 deg（末2s）<br>`terminal_pelvis_clearance` >= 0.65 m（末2s） | 30 s |

## Skydio X2（20项，全部 proposed / uncalibrated）

[完整任务](skydio_x2/1.0.0/catalog.json) · [一手来源记录](skydio_x2/1.0.0/sources.json)

| ID / 任务 | 具体操作与场景提议 | 指标与阈值（全部同时满足） | 期限 |
| --- | --- | --- | --- |
| X2-T01 地面起飞至悬停<br>Ground takeoff to hover | 从零推力地面支撑起飞至上方1 m；5 s后不再接地。 | `terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`completed_operation` == 1 binary<br>`terminal_body_tilt` <= 20 deg（末2s） | 60 s |
| X2-T02 受控降落并停桨<br>Controlled landing and motor stop | 落入0.6 m圈；非旋翼碰撞体持续支撑，旋翼不碰地。 | `completed_operation` == 1 binary<br>`touchdown_vertical_speed` <= 0.25 m/s<br>`terminal_total_thrust_command` <= 0.1 N（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`terminal_body_tilt` <= 20 deg（末2s） | 60 s |
| X2-T03 定点空中等待<br>Stationary airborne hold | 在初始[0,0,1] m保持60 s；不模拟无线电。 | `maximum_hold_position_error` <= 0.15 m（全程）<br>`maximum_hold_heading_error` <= 10 deg（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T04 升高—平飞—下降返航<br>Climb-transit-descend return | 先升至返航高度，再移向home，近home后下降。 | `completed_operation` == 1 binary<br>`terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T05 有序航点飞行<br>Ordered waypoint mission | 4个航点，每点位置≤0.20 m且朝向≤10°保持0.5 s。 | `ordered_waypoint_visits` == 4 waypoint<br>`terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T06 连续样条航线<br>Continuous cinematic path | 5–25 s时间参数曲线；按全部参考时刻计算误差。 | `spline_tracking_rmse` <= 0.2 m<br>`terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T07 定点环绕观察<br>Point-of-interest orbit | 半径1.5 m、高1.5 m；绕一圈并用机体朝向目标。 | `signed_orbit_progress` >= 6.283185307179586 rad<br>`maximum_orbit_radius_error` <= 0.2 m（全程）<br>`maximum_orbit_height_error` <= 0.15 m（全程）<br>`orbit_bearing_rmse` <= 12 deg<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T08 原地偏航跟踪目标<br>Track a moving bearing from a fixed point | 位置不动；5–25 s仅机体偏航跟随90°目标弧。 | `maximum_station_drift` <= 0.15 m（全程）<br>`bearing_tracking_rmse` <= 10 deg<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T09 相对移动目标伴飞<br>Subject-relative escort | 5–25 s维持目标后方1.5 m、上方1.5 m偏移。 | `escort_offset_rmse` <= 0.25 m<br>`minimum_subject_separation` >= 1.0 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T10 水平全景偏航序列<br>Horizontal panorama motion | 8个45°间隔朝向，分别≤8°保持0.5 s；恢复原朝向。 | `ordered_heading_stations` == 8 station<br>`maximum_panorama_drift` <= 0.15 m（全程）<br>`terminal_heading_error` <= 8 deg（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T11 接近结构并停稳<br>Structure approach and stop | 墙前0.8 m悬停；不采用产品的激活距离。 | `terminal_wall_standoff_error` <= 0.1 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`minimum_surface_clearance` >= 0.3 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T12 障碍绕行飞抵目标<br>Obstacle-aware transit | 绕过直线路径上的箱体，保持几何净距。 | `terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`minimum_obstacle_clearance` >= 0.2 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T13 高度上限内越障<br>Flight beneath a height bound | 越过0.4 m高障碍；穿越纵向平面时横向位于障碍投影内，全机碰撞几何在顶面上方；高度≤1.2 m。 | `terminal_position_error` <= 0.15 m（末2s）<br>`maximum_flight_height` <= 1.2 m（全程）<br>`minimum_barrier_clearance` >= 0.15 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程）<br>`completed_operation` == 1 binary | 60 s |
| X2-T14 平面测绘航带覆盖<br>Planar survey-lane coverage | 4条3 m长航带依次完整飞过；不评图像/GSD。 | `ordered_scan_lanes` == 4 lane<br>`maximum_scan_height_error` <= 0.15 m<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T15 多高度塔体观察<br>Multi-level tower inspection route | 4方位×3高度共12站；各站位置、朝向、速度同时满足。 | `distinct_tower_stations` == 12 station<br>`minimum_tower_clearance` >= 0.3 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T16 凹形结构观察位点<br>Concave-structure observation route | 6个视点含2个凹腔内；要求对相应表面几何无遮挡。 | `distinct_surface_viewpoints` == 6 viewpoint<br>`minimum_structure_clearance` >= 0.2 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T17 桥面下方巡检<br>Under-bridge inspection traverse | 桥下3站依次到达；不评不存在的向上云台。 | `ordered_underdeck_stations` == 3 station<br>`minimum_overhead_clearance` >= 0.4 m（全程）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T18 横向观测基线获取<br>Lateral baseline acquisition | 依次占据相距1 m侧向站位；目标保持水平朝向。 | `completed_operation` == 1 binary<br>`baseline_bearing_rmse` <= 10 deg<br>`achieved_lateral_baseline` >= 0.9 m<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T19 中断—集合—续扫<br>Pause-return-resume a scan route | 扫描站1、2→集合点停2 s→站3、4；不模拟换电。 | `completed_operation` == 1 binary<br>`terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |
| X2-T20 受限体积同口进出<br>Geofenced volume entry and exit | 从指定1 m开口进虚拟体积，到内站停1 s，同口退出。 | `completed_operation` == 1 binary<br>`forbidden_boundary_crossings` == 0 crossing<br>`terminal_position_error` <= 0.15 m（末2s）<br>`terminal_translation_speed` <= 0.1 m/s（末2s）<br>`terminal_body_tilt` <= 20 deg（末2s）<br>`forbidden_contact_samples` == 0 sample（全程） | 60 s |

## 本批验证范围

JSON与包接口检查覆盖非空且唯一task_id、完整调用包络、有限数值阈值、评分窗口与aggregation、可解析source_id及明确proposed身份。四包合计80项任务、294条评分条款。真实模型加载与短步进只检查模型资源和执行器接口可用性；不证明上述任务可达，也不形成任务成功率。

X2高度受限越障条款另做一次语义反例检查：侧绕可满足原终点、高度和净距条款，新增真正跨越障碍顶面的事件后该侧绕反例被排除；这不是物理任务运行。

A1侧卧恢复条款另做一次直接反例检查：水平躯干、四足接地但腹部贴地可满足原两个条款；新增末态躯干几何离地≥0.12 m后该反例被排除。这只是评分语义检查，没有将0.12 m校准为性能界限。
