# AutoAdapter 2.0 Tasks Library — Human-Readable Catalog

> **Document role:** human-readable view of the reviewed Tasks Library  
> **Framework authority:** [`AUTOADAPTER_2_AUTHORITY.md`](AUTOADAPTER_2_AUTHORITY.md)  
> **Catalog revision:** `0.2.0`  
> **Normative record language:** English  
> **Current population:** 28 reviewed SO-ARM101 task records

This document contains the task records that have completed user review for AutoAdapter 2.0. It is
designed for people to read and audit. It is not an executable task schema and does not by itself
freeze the future machine-readable file format.

Only an explicitly approved task record is added here. Candidate tasks, proposed difficulty labels,
and draft pass criteria remain in the review conversation until the user approves or modifies them.

**中文辅助说明。** 这是 Tasks Library 的人工可读版本，只保存已经人工审核通过的任务。
Codex 提出的候选任务、难度和 pass criterion 在确认前只留在讨论中；确认后才加入本文档。
本文档不是直接喂给 Generation 的输入文件，因为其中包含 Harness-private criterion。

---

## 1. Catalog Role and Information Views

Each admitted record combines information needed for research review with information that must
remain private during execution. The Framework must derive the permitted view for each consumer
rather than exposing this complete document.

| View | Information available | Recipient |
|---|---|---|
| Human review/catalog view | Complete approved record, including source basis, difficulty rationale, and pass criterion | Researchers and maintainers |
| Generation task view | Task description only | Generation subsystem |
| Demo task view | Current task description only | Demo Consumer |
| Evaluation view | Private pass criterion and required outcome evidence | Demo Evaluation Harness |

Here, `private` means private from Generation, the generated capability layer, and the Demo
Consumer. It remains readable to authorized human reviewers and the trusted evaluation code.

The catalog is a downstream application-task collection. It is not the Validation Task and
Pass-Threshold Library and is not an input collection for the validation-task generator described
in Section 4.2.2 of `AUTOADAPTER_2_AUTHORITY.md`.

---

## 2. Task Research, Review, and Admission

For each covered robot model and configuration, task construction follows this sequence:

1. Codex researches publicly accessible robotics benchmarks and published or otherwise public
   research, including the prior AutoAdapter benchmark where applicable.
2. Candidate tasks are adapted to the target robot rather than copied together with an upstream
   difficulty label or evaluation protocol.
3. Candidates are normalized and organized by task type or family. Curation aims for more than 20
   tasks per covered robot when public coverage and physical applicability support that target.
4. Codex proposes the task description, source basis, robot-conditioned difficulty, difficulty
   rationale, pass criterion, and evidence that an external Harness would need to evaluate it.
5. The user reviews each proposed record and may approve, modify, or reject it.
6. Only the final approved record is inserted into the robot's catalog below.
7. Five approved records may later be designated as the fixed General Demo `demo_tasks` set; all
   five are executed and none is treated as held out.

Difficulty is assigned to a `task × robot_configuration` pairing. It is not a global property of a
task and is never inherited directly from a source benchmark. The exact difficulty rubric must be
approved before a difficulty annotation is admitted.

Every admitted task has a pass criterion. The criterion must be evaluable by the external Demo
Evaluation Harness from independently acquired outcome evidence; a model or generated capability
function reporting success is not sufficient evidence by itself.

---

## 3. Human-Readable Record Layout

Tasks may be presented as grouped human-readable tables. Each admitted record contains:

- the Agent-visible task description;
- the reviewed robot model/configuration, task classification, robot-conditioned difficulty and
  rationale, and any fixed-Demo designation;
- the public source basis and robot-specific adaptation;
- the evaluation-private pass criterion; and
- the approval date or review note.

The `T01`–`T28` labels below identify this reviewed SO-ARM101 catalog batch. This presentation does
not by itself establish the future machine-readable identifier format, executable fields, trial
counts, reset rules, or an Oracle schema. Those contracts are reviewed separately. If task content
has not been approved, it is not inserted with a guessed or placeholder value.

---

## 4. Reviewed Task Catalog

| Robot configuration | Approved tasks | Fixed Demo tasks | Catalog status |
|---|---:|---:|---|
| SO-ARM101 — fixed-base manipulator with stock gripper | 28 | 0 | Reviewed catalog batch admitted |
| Additional confirmed configurations | 0 | 0 | Subsections added after approval |

### 4.1 SO-ARM101 — Fixed-Base Manipulator with Stock Gripper

**Admitted task count:** 28  
**Fixed Demo task count:** 0  
**Batch approval date:** 2026-08-09

#### 4.1.1 Configuration Boundary

This reviewed batch is conditioned on the fixed-base SO-ARM101 configuration with five arm joints
and one stock-gripper actuator. It is not described as a six-DoF arm. The base configuration does
not assume an onboard camera. A task therefore cannot silently depend on unprovided visual
recognition; the target object, target region, and other required scene facts must be available
through the run's approved observation interface. A later configuration such as SO-ARM101 with a
D405 camera requires a separate difficulty review.

The tasks use small, lightweight tabletop objects and fixtures adapted to the robot's reachable
workspace. A source benchmark establishes task provenance, not SO-ARM101 feasibility. Geometry-
sensitive tasks must still pass scene-specific reachability and repeated-execution checks before
they are selected for an executable experiment.

#### 4.1.2 Approved Robot-Conditioned Difficulty Scale

| Level | Definition for this SO-ARM101 configuration |
|---|---|
| D1 — Direct | One direct motion or broad-tolerance contact; no stable grasp or multi-stage manipulation. |
| D2 — Basic manipulation | One controlled interaction, stable grasp, or simple fixture transition with generous tolerance. |
| D3 — Multi-phase | Multiple manipulation phases, transport/release, pose maintenance, or moderately constrained geometry. |
| D4 — Precision or compound | Tight alignment, constrained contact, articulated pulling, multi-object ordering, clutter, or tool use. |

Difficulty and feasibility are distinct. A D4 task is not automatically infeasible, but tasks
whose rationale says that a pilot is required must not be treated as executable evidence until the
corresponding geometry and repeated-execution checks pass.

#### 4.1.3 Source Basis

- `AA1`: [prior AutoAdapter benchmark](https://github.com/981526092/auto-adapter/tree/585eb1f1fde33f17f5f9a1e169a18dd41f97b586)
- `RLB`: [RLBench task catalogue](https://github.com/stepjam/RLBench/tree/master/rlbench/tasks)
- `MS`: [ManiSkill tabletop gripper tasks](https://maniskill.readthedocs.io/en/latest/tasks/table_top_gripper/index.html)
- `LIB`: [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO)
- `CAL`: [CALVIN](https://github.com/mees/calvin)
- `RC`: [RoboCasa atomic tasks](https://robocasa.ai/docs/build/html/tasks/atomic_tasks.html)

These sources provide task concepts and public provenance. The admitted records below are
SO-ARM101-specific adaptations with independently reviewed difficulty and pass criteria; they are
not claims that an upstream task or its original evaluation protocol runs unchanged on SO-ARM101.

#### 4.1.4 Common Harness-Private Pass Gate

Every task-specific pass criterion below is conjoined with the following requirements:

- The verdict comes only from the external trusted Demo Evaluation Harness, never from the Demo
  Consumer's self-report or a capability return value.
- The success state persists for the dwell time stated in the task criterion.
- No forbidden collision, joint-limit violation, emergency stop, fixture overturning, or target
  object leaving the workspace occurs.
- `released` means that the gripper no longer supports the object.
- `settled` means that Harness-observed linear and angular velocities are below the task's frozen
  limits.
- Timeout, reset distribution, and scene-specific safety limits are fixed with the corresponding
  executable task assets.

The criterion text and evaluation-only outcome data are private from Generation, the generated
capability layer, and the Demo Consumer.

#### 4.1.5 Reach and Non-Prehensile Manipulation

| ID | Agent-visible description | Type | Source basis | Robot-conditioned difficulty and rationale | Harness-private pass criterion |
|---|---|---|---|---|---|
| T01 | Move the gripper reference point to the specified 3-D target and stop. | Reach | RLB `ReachTarget`; AA1 | D1 — Direct positioning with no grasp. | Tip-position error is at most 20 mm and tip speed is at most 10 mm/s continuously for 0.5 s. |
| T02 | Touch the specified face of the target object without moving the object. | Controlled contact | RLB reach/contact family | D1 — Short contact motion, but it requires contact evidence. | Intended tip–face contact persists for at least 0.3 s; object displacement is at most 10 mm; no other object is contacted. |
| T03 | Push the cube into the specified planar goal region. | Planar pushing | RLB `SlideBlockToTarget`; MS `PushCube`; AA1 | D1 — Broad-tolerance planar motion without grasping. | Cube center is within 25 mm of the goal center, remains supported by the table, and is settled for 1 s. |
| T04 | Push the cylinder laterally into the specified goal region while keeping it upright. | Planar pushing with orientation constraint | AA1 lateral-cylinder task | D2 — Contact direction and cylinder stability must both be controlled. | Cylinder center is within 25 mm of the goal; symmetry-axis tilt is at most 0.20 rad; the cylinder remains supported and settled for 1 s. |
| T05 | Roll the ball into the specified circular goal region. | Dynamic planar manipulation | MS `RollBall` | D2 — Object motion continues after contact, requiring anticipation. | Ball center remains within a 40 mm goal radius and its speed is at most 20 mm/s for 1 s. |
| T06 | Rotate the rectangular block to the specified planar orientation without moving it outside its local region. | Planar reorientation | MS planar-pose tasks | D3 — Requires selecting contact points to control yaw. | Yaw error is at most 10 degrees, center displacement from the allowed local region is at most 30 mm, and the block is settled for 1 s. |
| T07 | Push the T-shaped block until it matches the target silhouette. | Precision planar pose control | MS `PushT` | D4 — Translation and yaw must be controlled through repeated contact. | Top-view geometric overlap between the block and target silhouette is at least 90%; the block remains settled for 1 s. |

#### 4.1.6 Grasp, Lift, and Placement

| ID | Agent-visible description | Type | Source basis | Robot-conditioned difficulty and rationale | Harness-private pass criterion |
|---|---|---|---|---|---|
| T08 | Grasp the target cube and hold it securely just above the table. | Grasp and hold | AA1; MS `PickCubeSO100` | D2 — Tests stable stock-gripper closure without long transport. | The cube rises by at least 20 mm; gripper-relative slip is at most 5 mm over 1 s; the cube remains held. |
| T09 | Lift the target cube to the specified height and hold it there. | Lift | RLB `PickAndLiftSmall`; AA1 | D2 — Stable grasp plus vertical motion. | Cube-center height increases by at least 80 mm, the cube has no table contact, and it remains held for 1 s. |
| T10 | Move the grasped cube to the specified 3-D goal and keep holding it. | Grasped transport | MS `PickCubeSO100` | D3 — Combines grasp stability with multi-joint spatial transport. | Cube-center distance to the goal is at most 25 mm; the cube remains grasped and settled for 1 s. |
| T11 | Place the cube on the specified planar target and release it. | Pick and place | AA1; LIB goal tasks | D3 — Requires transport, controlled descent, release, and settling. | At least 90% of the cube footprint is inside the target; the cube is supported, released, and settled for 1 s. |
| T12 | Place the cube completely inside the shallow tray and release it. | Containment placement | AA1 tray tasks; LIB | D3 — Requires clearance-aware placement and release. | The entire projected cube footprint is inside the tray's inner boundary; the cube is tray-supported, released, and settled for 1 s. |
| T13 | Place the sphere inside the shallow bin and release it. | Dynamic containment placement | MS `PlaceSphere` | D3 — Release can cause rolling or escape. | The sphere satisfies the bin's geometric containment relation, is bin-supported and released, and remains below the allowed speed for 1 s. |
| T14 | Place the cylinder upright on the specified target and release it. | Orientation-constrained placement | AA1 | D3 — Position, symmetry-axis orientation, and release stability all matter. | Cylinder center is within 20 mm of the target; axis tilt is at most 0.20 rad; the cylinder is supported, released, and settled for 1 s. |

#### 4.1.7 Stacking and Assembly

| ID | Agent-visible description | Type | Source basis | Robot-conditioned difficulty and rationale | Harness-private pass criterion |
|---|---|---|---|---|---|
| T15 | Stack the target cube on top of the base cube and release it. | Stacking | MS `StackCube`; AA1 | D4 — Sensitive to depth error and release disturbance. | The top cube is supported by the base cube; XY offset is at most 25% of cube width; both cubes are released and settled for 1 s. |
| T16 | Raise the horizontal peg to an upright pose and release it on the table. | Object reorientation | MS `LiftPegUpright` | D4 — Requires a large orientation change and stable release. | Peg axis is within 0.10 rad of vertical; the peg base is table-supported; the peg is released and stable for 1 s. |
| T17 | Place the square ring over the specified square peg and release it. | Peg-and-ring assembly | RLB `InsertOntoSquarePeg` | D4 — Position and yaw must align throughout insertion. | The correct peg passes through the ring; ring bottom is at most 5 mm from its stop surface; tilt is at most 10 degrees; the ring is released and stable. |
| T18 | Insert the specified end of the peg into the side hole. | Side insertion | MS `PegInsertionSide` | D4 — Contact-rich alignment is difficult for a five-DoF arm; pilot required. | Insertion depth is at least 50% of the declared insertable segment; axis error is at most 10 degrees; fixture displacement is at most 5 mm; the state persists for 1 s. |
| T19 | Put the specified shape into its matching sorter slot and release it. | Shape insertion | RLB `PlaceShapeInShapeSorter` | D4 — Requires object selection, yaw alignment, and constrained insertion. | The correct shape is fully seated in the correct slot, with its top at or below the allowed entrance height; the shape is released and stable. |

#### 4.1.8 Articulated Fixtures

| ID | Agent-visible description | Type | Source basis | Robot-conditioned difficulty and rationale | Harness-private pass criterion |
|---|---|---|---|---|---|
| T20 | Press the specified large button until it activates. | Button actuation | RLB `PushButton` | D2 — Short motion, but contact direction and target discrimination matter. | Specified button displacement is at least 3 mm and activation persists for at least 0.25 s; no other button is activated. |
| T21 | Open the shallow drawer by its handle. | Articulated-object manipulation | RLB `OpenDrawer`; CAL; RC | D4 — Handle acquisition and constrained pulling are workspace-sensitive; pilot required. | Drawer joint reaches at least 80% of its declared open range; cabinet displacement is at most 10 mm; the drawer remains settled for 1 s. |
| T22 | Push the shallow drawer fully closed. | Articulated-object manipulation | RLB, CAL, and RC drawer tasks | D2 — Broad frontal pushing is easier than handle-based opening. | Drawer joint is at most 5% of its open range and remains settled for 1 s; the cabinet stays within its fixture tolerance. |
| T23 | Slide the tabletop door to its fully open position. | Sliding-fixture manipulation | CAL sliding-door tasks | D3 — Requires sustained constrained contact along one axis. | Door joint reaches at least 80% of its declared open range and remains settled for 1 s; fixture displacement is at most 10 mm. |
| T24 | Grasp the lid handle and remove the lid from the container. | Handle grasp and removal | RLB `TakeLidOffSaucepan`; RC | D3 — Requires stable handle grasp and vertical clearance. | The lid enters the declared off-container goal region; its lowest point clears the rim by at least 30 mm; container displacement is at most 10 mm; the condition persists for at least 0.5 s. |

#### 4.1.9 Multi-Object, Sequence, and Tool-Use Tasks

| ID | Agent-visible description | Type | Source basis | Robot-conditioned difficulty and rationale | Harness-private pass criterion |
|---|---|---|---|---|---|
| T25 | Place each of the two specified objects into its matching tray. | Two-object sorting | AA1; LIB; RC | D4 — Two complete pick-and-place cycles with distinct destinations. | Each object is fully contained in its assigned tray; both are released and settled for 1 s. A swapped assignment fails. |
| T26 | Place object A into the tray, then place object B into the tray. | Ordered multi-object manipulation | AA1; CAL sequences | D4 — Requires memory of task order and two reliable manipulation cycles. | The Harness event log records a persistent successful placement of A before B; the final state contains both objects, released and settled. |
| T27 | Put the specified object inside the open drawer and then close the drawer. | Compound fixture task | CAL; RC | D4 — Combines pick-and-place, containment, and articulated manipulation. | The object is contained in the drawer volume; drawer joint is at most 5% open; the object is released and the final state remains stable for 1 s. |
| T28 | Use the provided tool to pull the initially unreachable cube into the specified reachable region. | Tool use | MS `PullCubeTool` and `PokeCube` | D4 — Requires tool grasp, indirect contact, and compound planning; pilot required. | The cube enters the goal region; Harness contact history shows tool-mediated cube displacement; direct gripper–cube contact before goal entry causes failure; the cube settles for 1 s. |

#### 4.1.10 Fixed Demo Designation

No task in this batch has yet been designated as one of the five fixed General Demo tasks. Demo
selection is a separate reviewed decision made from admitted task records; it does not change the
task descriptions or private criteria above.

### 4.2 Additional Robot Configurations

A separate subsection is added for each confirmed robot configuration when its first task record is
approved. Task descriptions may be shared or adapted across robots, but difficulty and criteria are
reviewed for the target configuration before admission.

No additional robot-configuration task record has been admitted yet.
