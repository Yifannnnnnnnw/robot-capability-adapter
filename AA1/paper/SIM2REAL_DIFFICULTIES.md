# Sim-to-Real Difficulties — Why the Paper Cannot Claim Real-Robot Success (Yet)

Session date: 2026-06-03
Hardware: SO-ARM101 + Feetech STS3215 + RealSense D405 + Bedrock Claude Opus 4.8
Software: vector-os-nano backend, auto-adapter synthesized driver

## Summary

The simulation pipeline succeeds at 100% on pick/place/contact-rich/push tasks. The same agent + driver on the physical SO-ARM101 failed to grasp a 4-cm cube end-to-end, despite ~6 hours of iteration. The failures decompose into **four independent gaps**, each of which we either could not close in one session or required hand-tuning that contradicts the "automatic adapter" claim.

## Gap 1 — Servo position reproducibility (≥5-10 mm)

Sim: `commanded_joint = actual_joint`. No noise.
Real: STS3215 servos, commanded `[0.937, -0.345, -0.106, 1.061, -0.994]` rad, actually reach `[0.926, -0.306, -0.102, 1.052, -0.994]`. Per-joint error **1–4 degrees** under gravity-loaded conditions, propagating to **5–8 mm** end-effector position error.

We mitigated this with manual feed-forward offset `(-5, +3, +6) mm` (measured once via FK after a commanded move). After compensation, EE landed within 2 mm of target. But:
- The offset is pose-dependent (depends on which joints carry load), so a single compensation vector does not generalize.
- vector-os-nano hardcodes its own `+2 cm forward, +2 cm Y` offset for the same reason — this is bespoke per arm + per workspace.

## Gap 2 — Hand-eye calibration (camera ↔ base)

Sim: camera pose given by the MJCF; no perception transform needed.
Real: RealSense D405 is wrist-mounted, so the camera→base transform depends on the arm pose: `T_cam→base(q) = T_ee→base(q) · T_cam→ee`. `T_cam→ee` is a fixed rigid offset that must be calibrated.

Attempts in this session:
- **5 hand-picked candidate axis-aligned rotations** plus depth projection → 17 cm error in Z at the predicted cube location (off by table height).
- **24-rotation grid search using 4 small joint perturbations**: top stdev = 11 mm, but the EE motion was <1 cm per pose so the system was ill-conditioned; predicted Z was 13 cm above truth.
- **24-rotation grid search using 4 larger 5-cm base-frame perturbations**: top stdev = 92 mm; 2 of 4 poses had bedrock bbox saturating image edges, contaminating depth.
- **Kabsch from 7 known-good poses + kinesthetic-taught ground truth** *(attempted last; aborted)*: all 7 bboxes saturated the image edge because the cube had drifted out of FOV during prior open/close cycles.

What would have worked (and what vector-os-nano does): one-time workspace calibration with N=4–6 hand-labeled (cam_xyz, base_xyz) correspondences and an affine solve, then store the 4×4 matrix to `workspace_calibration.yaml`. This is "automatic *after the human places markers*" — not zero-touch.

## Gap 3 — Depth sensor effective range

D405 spec range is 7 cm – 50 cm. With `depth_units = 1e-4 m / unit` (we initially misread as `1e-3` and got 1.0 m depths when the actual was 0.1 m), the data is now consistent. But:
- At wrist mount with EE at z = 0.24 m, scene depths are 0.06 – 0.15 m — right at the close end of the working range.
- The gripper fingers themselves (≈3–7 cm from the lens) read as `NaN`/zero because they are below `min_range`, so any bbox that includes the gripper jaws gives a corrupted depth.
- Glare on the black perforated lid + gold rim caused multiple bboxes where Bedrock returned tiny strips at image edges that have nothing to do with the cube.

We did not have time to add reflective-target mitigation (matte tape, depth-validity masking, or a dedicated detection target).

## Gap 4 — Gripper closing reliability

Sim: gripper is an idealized constraint; "close" reliably grips any object within finger width.
Real:
- A *single* `gripper.close()` produced encoder = 1337 (fully closed, no object detected) even when the cube was visibly between the fingers — likely the fingers slid past the cube on the way down because cube width (~3 cm) is small relative to gripper finger length (~6 cm).
- The kinesthetically-taught grasp gave encoder = 1474 (gripping). When we replayed the joint trajectory, the EE landed at the correct position (≤2 mm), the gripper still missed, because finger-cube lateral alignment requires sub-mm precision and there is no feedback (no force sensor, no tactile, no slip detection).
- vector-os-nano's production fix is `open → wait → close ×3` plus the `+2 cm Y` empirical offset. We did not implement the multi-close retry pattern this session.

## Underlying Structural Issue — Real Loop Has No Feedback

The simulation pipeline is fully closed-loop:
- IK target → physics step → MJCF state read → success evaluator.

The real pipeline has only the following sensing:
- Joint encoders (open-loop position; tells us *where we ended up*, not *whether we did the task*).
- RealSense (high noise at close range, glare on metals, FOV management is manual).
- No force / tactile / proprioceptive contact sensing.

So even with perfect perception and perfect calibration, *whether the grasp succeeded* must be inferred from a single integer (gripper encoder ∈ [1332, 2500]). That measurement is too coarse to drive an outer retry loop without additional sensing.

## What This Means for the Paper

Auto-Adapter's claim is that an LLM agent can **synthesize a robot driver from MJCF + a one-line spec** and have it pass the spec's evaluators. That claim is well-supported in simulation across 8 robots, 5 task suites, 5 LLMs (~$160 of API + ~7.8 min wall-clock per robot).

The claim **does not extend to physical hardware** without an additional engineering layer — workspace calibration, servo bias compensation, gripper retry policy, and a force-/vision-based outer feedback loop. These pieces:
- Are independent of the LLM (would be needed for any hand-written driver too),
- Take a human O(hours) of per-arm bring-up time,
- Are not "automatic" in the sense the paper uses the word.

The honest framing is what we already have in §6: **simulation as a curriculum for LLM-synthesized control**, with sim-to-real explicitly out of scope and called out as future work. Trying to insert a real-robot success story would either require ≥1 week of additional bring-up or a much more constrained demo (e.g., pre-recorded teleop trajectories replayed open-loop, which is not what "Auto-Adapter onboards a new robot" should mean).

## What We Did Accomplish on Real Hardware

For the future-work section:
- Synthesized real-robot driver (`real_robot/real_so101.py`) from a one-line spec, using auto-adapter's GENERATE phase against the SO-101 MJCF + URDF.
- Wired in vector-os-nano as the motion backend (IK, gripper, serial bus) and Bedrock Opus 4.8 as the perception VLM.
- Demonstrated **arm reaches a kinesthetically-taught grasp pose within 2 mm of demo target** after a one-shot servo bias correction.
- Confirmed **D405 + Bedrock can localize objects in eye-in-hand view** (cube bbox detection works when cube is in FOV and not occluded by metal glare).
- Identified the **four gaps above as the critical-path items** for a future sim-to-real claim.

## Recommended Next Session (1-day plan if revisited)

1. Print or fix a calibration target (ArUco / checkerboard) at a known position relative to the SO-101 base.
2. Auto-collect N=6 (cam_xyz, base_xyz) correspondences via the target — no kinesthetic teach drift.
3. Solve affine, store `workspace_calibration.yaml` per vector-os-nano's format.
4. Implement the open→close×3 multi-pulse pattern.
5. Add an iterative IK servo correction step: command → read FK → re-command with residual.
6. Test on N=20 random cube placements; report success rate.

If even one of these fails to converge by end-of-day, sim-to-real stays out of the paper.
