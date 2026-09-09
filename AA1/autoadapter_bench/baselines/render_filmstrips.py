#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render task-execution filmstrips: 5 frames per task showing the
agent-synthesized driver moving through a real MuJoCo rollout.

Outputs to paper/latex/figures/:
  film_so101_reach_0.png .. film_so101_reach_4.png   (Reach +5 cm in X)
  film_piper_pick_0.png  .. film_piper_pick_4.png    (Pick cube)
  film_go2_walk_0.png    .. film_go2_walk_4.png      (Stand + walk forward)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
ART = REPO_ROOT / "artifacts"
OUT = REPO_ROOT / "paper" / "latex" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = 480, 320


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_cam(distance, elevation, azimuth, lookat):
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.distance = float(distance)
    cam.elevation = float(elevation)
    cam.azimuth = float(azimuth)
    cam.lookat[:] = np.array(lookat, dtype=float)
    return cam


def render(model, data, cam, out_path):
    with mujoco.Renderer(model, height=HEIGHT, width=WIDTH) as r:
        r.update_scene(data, camera=cam)
        img = r.render()
    Image.fromarray(img).save(out_path)
    print(f"  wrote {out_path.name}  ({img.shape[1]}x{img.shape[0]})")


def step_partial(model, data, q_target, n_steps, joint_ids, arm_names):
    """Linearly interpolate ctrl from current to q_target over n_steps."""
    q0 = np.array(data.qpos[joint_ids])
    for s in range(n_steps):
        alpha = (s + 1) / n_steps
        q = (1 - alpha) * q0 + alpha * q_target
        for i, name in enumerate(arm_names):
            aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"act_{name}")
            if aid >= 0:
                data.ctrl[aid] = q[i]
        mujoco.mj_step(model, data)


def filmstrip_so101():
    print("\n[SO-101 Reach +5 cm in X]")
    art_dir = ART / "auto_adapter_from_scratch_so101_artifacts"
    mod = load_module("so101_driver", art_dir / "driver_from_scratch.py")
    robot = mod.Robot.build_from_mjcf(str(art_dir / "mjcf.xml"))
    robot.home()
    cam = make_cam(0.48, -22, 145, (0.24, 0.0, 0.12))

    # Frame 0: post-home
    render(robot.model, robot.data, cam, OUT / "film_so101_reach_0.png")

    # Compute the +5cm-in-X target from current EE pose
    xyz0, _ = robot.get_ee_pose()
    target = xyz0 + np.array([0.05, 0.0, 0.0])
    q_target, ok, err = robot.inverse_kinematics(target, q_init=robot.get_joint_positions())
    if not ok:
        print(f"  WARN IK miss: err={err:.4f}")

    # Frame 1: 33% of the way
    timestep = robot.model.opt.timestep
    total_steps = int(2.0 / timestep)
    chunk = total_steps // 3
    step_partial(robot.model, robot.data, q_target, chunk, robot.joint_ids, robot.arm_joint_names)
    render(robot.model, robot.data, cam, OUT / "film_so101_reach_1.png")

    # Frame 2: 66% of the way
    q_now = np.array(robot.data.qpos[robot.joint_ids])
    step_partial(robot.model, robot.data, q_target, chunk, robot.joint_ids, robot.arm_joint_names)
    render(robot.model, robot.data, cam, OUT / "film_so101_reach_2.png")

    # Frame 3: 100% at target
    step_partial(robot.model, robot.data, q_target, total_steps - 2 * chunk, robot.joint_ids, robot.arm_joint_names)
    render(robot.model, robot.data, cam, OUT / "film_so101_reach_3.png")

    # Frame 4: return-to-home for the "hold + return" final
    home_q = np.zeros_like(q_target)
    step_partial(robot.model, robot.data, home_q, total_steps, robot.joint_ids, robot.arm_joint_names)
    render(robot.model, robot.data, cam, OUT / "film_so101_reach_4.png")


def filmstrip_piper():
    print("\n[Piper pick cube from pickbench]")
    art_dir = ART / "from_scratch_piper_pickbench"
    mod = load_module("piper_driver", art_dir / "driver_from_scratch.py")
    robot = mod.Robot.build_from_mjcf(str(art_dir / "mjcf.xml"))
    robot.home()
    cam = make_cam(1.0, -25, 135, (0.35, 0.0, 0.20))

    cube_label = "cube_green"
    cube_xyz = robot.get_object_position(cube_label)
    print(f"  target cube: {cube_label} at {cube_xyz}")

    # Frame 0: home, cube visible
    robot.gripper_open()
    render(robot.model, robot.data, cam, OUT / "film_piper_pick_0.png")

    # Frame 1: pre-grasp (8cm above cube)
    try:
        robot.move_cartesian(cube_xyz + np.array([0.0, 0.0, 0.08]), duration=1.5)
    except Exception as e:
        print(f"  move pre-grasp failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_piper_pick_1.png")

    # Frame 2: descend to cube (0.5cm above center, well within 2cm grasp threshold)
    try:
        robot.move_cartesian(cube_xyz + np.array([0.0, 0.0, 0.005]), duration=1.0)
    except Exception as e:
        print(f"  move descend failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_piper_pick_2.png")

    # Frame 3: close gripper (will weld cube to link6 if within threshold)
    try:
        ok = robot.gripper_close()
        print(f"  gripper_close → {ok}")
    except Exception as e:
        print(f"  gripper_close failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_piper_pick_3.png")

    # Frame 4: lift 10cm above original cube position
    try:
        robot.move_cartesian(cube_xyz + np.array([0.0, 0.0, 0.12]), duration=1.0)
    except Exception as e:
        print(f"  lift failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_piper_pick_4.png")


def filmstrip_go2():
    """Go2 stand-up sequence (5 frames at evenly-spaced sub-durations).
    walk_forward is known to fail on Go2 (canonical: 1/2 simple tasks);
    stand_up is the validated-passing primitive, so we capture that.
    """
    print("\n[Go2 stand-up (validated primitive)]")
    art_dir = ART / "auto_adapter_from_scratch_go2_artifacts"
    mod = load_module("go2_driver", art_dir / "driver_from_scratch.py")
    mjcf_resolved = (REPO_ROOT / "assets" / "mjcf" / "go2" / "go2_scene.xml").resolve()
    robot = mod.Robot.build_from_mjcf(str(mjcf_resolved))
    cam = make_cam(1.6, -15, 120, (0.0, 0.0, 0.2))

    # First put the dog into the sit pose so stand_up is a visible transition
    try:
        robot.sit(duration=1.0)
        robot.step(100)
    except Exception as e:
        print(f"  sit setup failed: {e}")

    # Frame 0: sitting
    render(robot.model, robot.data, cam, OUT / "film_go2_walk_0.png")

    # Stand-up split into 3 quarters so we capture sit → mid → stand
    try:
        robot.stand_up(duration=0.5)
    except Exception as e:
        print(f"  stand quarter-1 failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_go2_walk_1.png")

    try:
        robot.stand_up(duration=0.5)
    except Exception as e:
        print(f"  stand quarter-2 failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_go2_walk_2.png")

    try:
        robot.stand_up(duration=1.0)
        robot.step(200)
    except Exception as e:
        print(f"  stand settle failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_go2_walk_3.png")

    # Frame 4: sit again (sit → stand → sit cycle)
    try:
        robot.sit(duration=1.0)
        robot.step(100)
    except Exception as e:
        print(f"  sit final failed: {e}")
    render(robot.model, robot.data, cam, OUT / "film_go2_walk_4.png")


def filmstrip_letter():
    """SO-101 trace-the-L: down 4 cm -> forward (+X) 4 cm -> return home.
    All corners are within SO-101's workspace (empirically verified), so this
    renders the ACTUAL feasible trajectory the agent executes (the earlier
    '15 cm down' L was unreachable and produced no strokes)."""
    print("\n[SO-101 trace L: down 4 cm + forward 4 cm]")
    art_dir = ART / "auto_adapter_from_scratch_so101_artifacts"
    mod = load_module("so101_driver", art_dir / "driver_from_scratch.py")
    robot = mod.Robot.build_from_mjcf(str(art_dir / "mjcf.xml"))
    robot.home()
    cam = make_cam(0.48, -22, 145, (0.24, 0.0, 0.12))
    home_ee, _ = robot.get_ee_pose()
    c1 = home_ee + np.array([0.0, 0.0, -0.04])   # corner 1: down 4 cm
    c2 = home_ee + np.array([0.04, 0.0, -0.04])  # corner 2: + forward 4 cm
    timestep = robot.model.opt.timestep
    steps = int(1.5 / timestep)

    def goto(target, n):
        q, ok, err = robot.inverse_kinematics(target, q_init=robot.get_joint_positions())
        if not ok:
            print(f"  WARN IK miss: err={err:.4f} for {target}")
        step_partial(robot.model, robot.data, q, n, robot.joint_ids, robot.arm_joint_names)

    render(robot.model, robot.data, cam, OUT / "film_so101_letter_0.png")   # home
    goto(c1, steps)
    render(robot.model, robot.data, cam, OUT / "film_so101_letter_1.png")   # corner 1 (down)
    goto(c2, steps)
    render(robot.model, robot.data, cam, OUT / "film_so101_letter_2.png")   # corner 2 (forward)
    goto(home_ee, steps // 2)
    render(robot.model, robot.data, cam, OUT / "film_so101_letter_3.png")   # returning
    step_partial(robot.model, robot.data, np.zeros(len(robot.joint_ids)),
                 steps, robot.joint_ids, robot.arm_joint_names)
    render(robot.model, robot.data, cam, OUT / "film_so101_letter_4.png")   # home



def filmstrip_skydio():
    """Skydio X2: ground -> takeoff 0.5 m -> waypoint (0.4,0,0.5) -> return -> land.
    Mirrors the aerial-suite go-to task (Appendix J); executes the synthesized
    flight controller live."""
    print("\n[Skydio X2 takeoff / waypoint / land]")
    art_dir = ART / "from_scratch_aerial_v2" / "skydio_x2"
    mod = load_module("skydio_driver", art_dir / "driver_from_scratch.py")
    scene = (REPO_ROOT / "assets" / "mjcf" / "skydio_x2" / "scene.xml").resolve()
    robot = mod.Robot.build_from_mjcf(str(scene))
    robot.home()
    cam = make_cam(1.25, -12, 135, (0.18, 0.0, 0.32))
    render(robot.model, robot.data, cam, OUT / "film_skydio_0.png")   # on ground
    robot.takeoff(0.5)
    render(robot.model, robot.data, cam, OUT / "film_skydio_1.png")   # hover 0.5 m
    robot.move_to(0.4, 0.0, 0.5)
    render(robot.model, robot.data, cam, OUT / "film_skydio_2.png")   # waypoint
    robot.move_to(0.0, 0.0, 0.5)
    render(robot.model, robot.data, cam, OUT / "film_skydio_3.png")   # return
    robot.land()
    render(robot.model, robot.data, cam, OUT / "film_skydio_4.png")   # landed


def filmstrip_h1():
    """Unitree H1: squat-recover cycle. Captures mid-motion frames by hooking
    mujoco.mj_step during the driver's own squat() call."""
    print("\n[H1 squat-recover]")
    art_dir = ART / "from_scratch_humanoid_v3" / "h1"
    mod = load_module("h1_driver", art_dir / "driver_from_scratch.py")
    scene = (REPO_ROOT / "assets" / "mjcf" / "h1" / "scene.xml").resolve()
    robot = mod.Robot.build_from_mjcf(str(scene))
    robot.home()
    robot.stand_balance(1.0)
    cam = make_cam(1.9, -8, 125, (0.0, 0.0, 0.80))
    render(robot.model, robot.data, cam, OUT / "film_h1_0.png")       # standing

    # capture frames during squat(): descend / bottom / rise / recovered
    secs, dt = 3.0, robot.model.opt.timestep
    n_total = int(secs / dt)
    targets = {int(n_total * f): i for f, i in
               [(0.25, 1), (0.50, 2), (0.78, 3)]}
    counter = {"n": 0}
    real_step = mujoco.mj_step

    def hooked(model, data, nstep=1):
        real_step(model, data, nstep)
        counter["n"] += 1
        if counter["n"] in targets:
            render(model, data, cam, OUT / f"film_h1_{targets[counter['n']]}.png")

    mujoco.mj_step = hooked
    try:
        robot.squat(depth=0.15, secs=secs)
    finally:
        mujoco.mj_step = real_step
    render(robot.model, robot.data, cam, OUT / "film_h1_4.png")       # recovered



def filmstrip_swap():
    """SO-101 swap_banana_bottle (contact-rich suite, A-A 5/5): re-executes the
    graded swap with the reference driver's weld grasp."""
    print("\n[SO-101 swap_banana_bottle]")
    import os
    art_dir = ART / "auto_adapter_so101_v2_artifacts"
    sys.path.insert(0, str(REPO_ROOT))
    cwd = os.getcwd()
    os.chdir(art_dir)
    try:
        mod = load_module("so101_v2_driver", art_dir / "driver.py")
        robot = mod.build()
    finally:
        os.chdir(cwd)
    robot.home()
    cam = make_cam(0.62, -28, 130, (0.24, 0.02, 0.10))

    banana0 = robot.get_object_position("banana").copy()
    bottle0 = robot.get_object_position("bottle").copy()
    park = np.array([0.10, 0.20, 0.10])

    def pick(obj_xyz):
        robot.gripper_open()
        robot.move_cartesian(obj_xyz + np.array([0.0, 0.0, 0.06]), duration=1.0)
        robot.move_cartesian(obj_xyz + np.array([0.0, 0.0, 0.01]), duration=0.8)
        robot.gripper_close()
        robot.move_cartesian(obj_xyz + np.array([0.0, 0.0, 0.10]), duration=0.8)

    def place(xy, z=0.03):
        robot.move_cartesian(np.array([xy[0], xy[1], 0.10]), duration=1.0)
        robot.move_cartesian(np.array([xy[0], xy[1], z]), duration=0.8)
        robot.gripper_open()
        robot.move_cartesian(np.array([xy[0], xy[1], 0.10]), duration=0.6)

    render(robot.model, robot.data, cam, OUT / "film_swap_0.png")     # initial scene
    pick(banana0)
    render(robot.model, robot.data, cam, OUT / "film_swap_1.png")     # banana lifted
    place(park[:2])
    pick(robot.get_object_position("bottle").copy())
    render(robot.model, robot.data, cam, OUT / "film_swap_2.png")     # bottle lifted, banana parked
    place(banana0[:2], z=0.055)
    pick(robot.get_object_position("banana").copy())
    place(bottle0[:2])
    render(robot.model, robot.data, cam, OUT / "film_swap_3.png")     # both relocated
    robot.home()
    render(robot.model, robot.data, cam, OUT / "film_swap_4.png")     # swapped, home
    b1 = robot.get_object_position("banana")
    t1 = robot.get_object_position("bottle")
    print(f"  banana {banana0[:2].round(3)} -> {b1[:2].round(3)} (target {bottle0[:2].round(3)})")
    print(f"  bottle {bottle0[:2].round(3)} -> {t1[:2].round(3)} (target {banana0[:2].round(3)})")



def filmstrip_anymal():
    """ANYmal-C sit -> stand -> walk_forward (quadruped suite; A-A 5/5 on
    sit_then_stand_to_nominal and walk_forward_10cm)."""
    print("\n[ANYmal-C sit/stand/walk]")
    art_dir = ART / "from_scratch_quad_xmodel" / "anymal_sonnet45"
    mod = load_module("anymal_driver", art_dir / "driver_from_scratch.py")
    scene = (REPO_ROOT / "assets" / "mjcf" / "anybotics_anymal_c" / "scene.xml").resolve()
    robot = mod.Robot.build_from_mjcf(str(scene))
    robot.home()
    cam = make_cam(2.2, -14, 115, (0.35, 0.0, 0.35))
    robot.stand_up(duration=2.0)
    p0 = robot.get_base_pose()[0].copy()
    render(robot.model, robot.data, cam, OUT / "film_anymal_0.png")  # standing
    for i in (1, 2, 3, 4):
        robot.walk_forward(secs=2.0, speed=0.2)
        render(robot.model, robot.data, cam, OUT / f"film_anymal_{i}.png")
    p1 = robot.get_base_pose()[0]
    import numpy as _np
    print(f"  walked {float(_np.linalg.norm(_np.asarray(p1[:2]) - _np.asarray(p0[:2]))):.3f} m, height {robot.get_body_height():.2f} m")


def filmstrip_patrol():
    """Skydio X2 box_patrol (aerial hard suite): takeoff 0.6 m then a 0.4 m
    square: (0.4,0,0.6)->(0.4,0.4,0.6)->(0,0.4,0.6)->(0,0,0.6)."""
    print("\n[Skydio X2 box patrol]")
    art_dir = ART / "from_scratch_aerial_v2" / "skydio_x2"
    mod = load_module("skydio_driver2", art_dir / "driver_from_scratch.py")
    scene = (REPO_ROOT / "assets" / "mjcf" / "skydio_x2" / "scene.xml").resolve()
    robot = mod.Robot.build_from_mjcf(str(scene))
    robot.home()
    cam = make_cam(1.30, -16, 150, (0.20, 0.18, 0.42))
    robot.takeoff(0.6)
    render(robot.model, robot.data, cam, OUT / "film_patrol_0.png")  # hover 0.6
    robot.move_to(0.4, 0.0, 0.6)
    render(robot.model, robot.data, cam, OUT / "film_patrol_1.png")  # corner 1
    robot.move_to(0.4, 0.4, 0.6)
    render(robot.model, robot.data, cam, OUT / "film_patrol_2.png")  # corner 2
    robot.move_to(0.0, 0.4, 0.6)
    render(robot.model, robot.data, cam, OUT / "film_patrol_3.png")  # corner 3
    robot.move_to(0.0, 0.0, 0.6)
    render(robot.model, robot.data, cam, OUT / "film_patrol_4.png")  # square closed


def main() -> int:
    try:
        filmstrip_so101()
    except Exception as e:
        print(f"  SO-101 filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_letter()
    except Exception as e:
        print(f"  SO-101 letter filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_piper()
    except Exception as e:
        print(f"  Piper filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_go2()
    except Exception as e:
        print(f"  Go2 filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_skydio()
    except Exception as e:
        print(f"  Skydio filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_swap()
    except Exception as e:
        print(f"  Swap filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_anymal()
    except Exception as e:
        print(f"  ANYmal filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_patrol()
    except Exception as e:
        print(f"  Patrol filmstrip FAILED: {type(e).__name__}: {e}")
    try:
        filmstrip_h1()
    except Exception as e:
        print(f"  H1 filmstrip FAILED: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
