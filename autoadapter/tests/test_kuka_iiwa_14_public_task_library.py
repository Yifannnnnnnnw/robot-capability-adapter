from __future__ import annotations

import json
import unittest
from pathlib import Path

from autoadapter2.libraries.robot_package import _validate_sources, _validate_tasks


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "libraries" / "robots" / "kuka_iiwa_14" / "1.0.0"
TASKS_ROOT = PACKAGE_ROOT / "tasks"
RUNNABLE_INDEX_PATH = ROOT / "libraries" / "robots" / "index.json"
FRANKA_CATALOG_PATH = (
    ROOT / "libraries" / "robots" / "franka_panda" / "1.0.0" / "tasks" / "catalog.json"
)

PINNED_COMMIT = "7ea2b501c4a698c8533cdc55a396fe2734e2649d"
SNAPSHOT_ID = "kuka-iiwa-14-metaworld-source-protocols-2026-08-20-v2"
P = (
    "The canonical KUKA iiwa 14 is a fixed-base 7-DoF serial arm with "
    "attachment_site on link7, one contact-enabled link7 sphere of radius "
    "0.06 m, and no gripper."
)

EXPECTED_SOURCE_DOCUMENT = {
    "robot_configuration_id": "kuka_iiwa_14",
    "package_version": "1.0.0",
    "sources": [
        {
            "source_id": "metaworld_repo",
            "title": "Meta-World",
            "organization": "Farama Foundation",
            "version_or_date": f"commit {PINNED_COMMIT} (2026-06-28)",
            "locator": (
                "https://github.com/Farama-Foundation/Metaworld/tree/"
                f"{PINNED_COMMIT}"
            ),
            "specific_reference": (
                f"Pinned official repository commit {PINNED_COMMIT}; v3 task "
                "evaluation implementations are under metaworld/envs/ and are "
                "cited by file and evaluation definition in each scoring clause."
            ),
        }
    ],
}

EXPECTED_TASK_IDS = [
    "mw_reach_target",
    "mw_push_to_goal",
    "mw_push_wall",
    "mw_sweep_into_goal",
    "mw_drawer_open",
    "mw_drawer_close",
    "mw_button_press",
    "mw_button_press_topdown",
    "mw_handle_press",
    "mw_door_open",
    "mw_door_close",
    "mw_faucet_open",
    "mw_dial_turn",
    "mw_lever_pull",
    "mw_door_lock",
    "mw_door_unlock",
    "mw_faucet_close",
    "mw_soccer",
    "mw_window_open",
    "mw_window_close",
]

COMMON_EVALUATION_REF = {
    "source_id": "metaworld_repo",
    "specific_reference": (
        'docs/evaluation/evaluation.md, lines 9-23 at pinned commit: each '
        'environment publishes info["success"]; MT1/MT10/MT50 count success if '
        "that flag is 1 at any point during an episode and average success "
        "across tasks and 50 goal episodes."
    ),
    "support": "adapted",
    "adaptation": (
        "Use the cited per-task success predicate but require it at the terminal "
        "state of one private trial. This terminal-state rule is stricter than "
        "Meta-World's any-point episode rule; single_trial replaces the "
        "benchmark's 50-goal average and must not be reported as Meta-World "
        "success rate."
    ),
}

INHERITED_DRAWER_REF = {
    "source_id": "metaworld_repo",
    "specific_reference": (
        "metaworld/sawyer_xyz_env.py, class SawyerXYZEnv line 156 at pinned "
        "commit: TARGET_RADIUS = 0.05."
    ),
    "support": "adapted",
    "adaptation": (
        "Resolve the inherited source constant to 0.05 m before adding the "
        "source 0.015 m margin; no tolerance is invented."
    ),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _array_property(description: str) -> dict:
    return {
        "type": "array",
        "items": {"type": "number"},
        "length": 3,
        "unit": "m",
        "frame": "world",
        "description": description,
    }


COMMON_PROPERTIES = {
    "start_position": _array_property(
        "Initial world-frame position of the affected task entity or fixture "
        "feature; it is not an end-effector waypoint."
    ),
    "contact_position": _array_property(
        "Initial intended world-frame center position of the named "
        "link7_contact_geom sphere when physical task contact is established."
    ),
    "target_position": _array_property(
        "Desired terminal world-frame position of the affected task entity or "
        "fixture feature; it is an attachment_site waypoint only for "
        "mw_reach_target."
    ),
    "target_angle": {
        "type": "number",
        "unit": "rad",
        "frame": "task_fixture",
        "description": (
            "Desired terminal angle of the affected task fixture; it is not a "
            "KUKA joint target."
        ),
    },
    "tool_target_position": _array_property(
        "Final commanded world-frame center position of link7_contact_geom used "
        "to drive the contacted task entity toward its task target."
    ),
    "route_position": _array_property(
        "Intermediate world-frame center position of link7_contact_geom used "
        "while maintaining or renewing the required physical contact route."
    ),
}

REQUIRED_PARAMETERS = {
    "mw_reach_target": ["target_position"],
    "mw_push_to_goal": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_push_wall": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_sweep_into_goal": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_drawer_open": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_drawer_close": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_button_press": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_button_press_topdown": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_handle_press": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_door_open": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_door_close": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_faucet_open": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_dial_turn": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_lever_pull": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "target_angle",
        "route_position",
    ],
    "mw_door_lock": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_door_unlock": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_faucet_close": [
        "contact_position",
        "target_position",
        "tool_target_position",
        "route_position",
    ],
    "mw_soccer": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_window_open": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
    "mw_window_close": [
        "contact_position",
        "target_position",
        "tool_target_position",
    ],
}

APPLICABILITY_SUFFIXES = {
    "mw_reach_target": (
        "It can place attachment_site at reachable tabletop target positions "
        "without manipulating an object."
    ),
    "mw_push_to_goal": (
        "The link7 sphere can make planar contact with a small rigid object and "
        "push it across a tabletop using contact only."
    ),
    "mw_push_wall": (
        "The link7 sphere can repeatedly contact a small rigid object and drive "
        "it along a physical route around a fixed obstacle."
    ),
    "mw_sweep_into_goal": (
        "The link7 sphere can make lateral sweeping contact with a small tabletop "
        "object without retaining it."
    ),
    "mw_drawer_open": (
        "The link7 sphere can open a constrained drawer when a collidable handle "
        "face is reachable from the side opposite the opening direction, allowing "
        "sustained contact to drive the slide."
    ),
    "mw_drawer_close": (
        "The link7 sphere can contact a drawer front or handle and push the "
        "constrained slide toward its closed stop."
    ),
    "mw_button_press": (
        "The link7 sphere can press a reachable front-facing constrained button."
    ),
    "mw_button_press_topdown": (
        "The link7 sphere can approach and press a reachable constrained button "
        "from above."
    ),
    "mw_handle_press": (
        "The link7 sphere can press a reachable face on a constrained vertical "
        "handle."
    ),
    "mw_door_open": (
        "The link7 sphere can open a hinged door when a reachable contact face "
        "permits sustained contact along the hinge arc."
    ),
    "mw_door_close": (
        "The link7 sphere can push a reachable door edge or handle toward a "
        "closed stop."
    ),
    "mw_faucet_open": (
        "The link7 sphere can rotate a faucet when an accessible radial feature "
        "permits sustained tangential contact through the opening arc."
    ),
    "mw_dial_turn": (
        "The link7 sphere can rotate a dial when an accessible rim or tab permits "
        "sustained tangential contact."
    ),
    "mw_lever_pull": (
        "The link7 sphere can drive a hinged lever through its required arc when "
        "the lever exposes a reachable collidable contact feature."
    ),
    "mw_door_lock": (
        "The link7 sphere can press a constrained lock feature when it exposes a "
        "reachable collidable face along the locking axis."
    ),
    "mw_door_unlock": (
        "The link7 sphere can press a constrained lock feature when it exposes a "
        "reachable collidable face along the unlocking axis."
    ),
    "mw_faucet_close": (
        "The link7 sphere can rotate a faucet when an accessible radial feature "
        "permits sustained tangential contact through the closing arc."
    ),
    "mw_soccer": (
        "The link7 sphere can contact and move a free rolling ball toward a "
        "physical goal fixture without retaining the ball."
    ),
    "mw_window_open": (
        "The link7 sphere can open a constrained sliding window when the handle "
        "exposes a reachable face opposite the opening direction."
    ),
    "mw_window_close": (
        "The link7 sphere can push a reachable sliding-window handle toward its "
        "closed stop."
    ),
}

EXPECTED_ADAPTATIONS = {
    "mw_reach_target": (
        "Replace the Sawyer body and benchmark scene with the canonical KUKA iiwa "
        "14 Direct MuJoCo model and a private tabletop target. Measure "
        "attachment_site against the target in the common world metre frame and "
        "preserve the source Euclidean metric, comparator, and 0.05 m threshold."
    ),
    "mw_push_to_goal": (
        "Use a private rigid object and goal fixture with the canonical KUKA iiwa "
        "14 model. Drive object motion only through link7 sphere contact and "
        "preserve the source Euclidean object-to-goal metric and 0.05 m threshold."
    ),
    "mw_push_wall": (
        "Use a private wall, object, and goal with the canonical KUKA iiwa 14 "
        "model. Preserve the physical wall-routing obligation, Euclidean "
        "object-to-goal metric, and 0.07 m threshold."
    ),
    "mw_sweep_into_goal": (
        "Use a private free object, collidable tabletop opening, and lower catch "
        "surface. Preserve the source contact-based sweep operation, Euclidean "
        "object-to-goal metric, and 0.05 m threshold."
    ),
    "mw_drawer_open": (
        "Use a private sliding drawer whose opposite handle face is reachable by "
        "the link7 sphere. Preserve the source Euclidean handle-to-open-target "
        "metric and 0.03 m threshold; no retained object hold is substituted."
    ),
    "mw_drawer_close": (
        "Use a private drawer fixture and preserve the source expression "
        "TARGET_RADIUS + 0.015, where inherited TARGET_RADIUS is 0.05 m; encode "
        "only the resulting 0.065 m threshold."
    ),
    "mw_button_press": (
        "Use a private y-slide button and preserve the source absolute y-axis "
        "error metric and 0.02 m threshold."
    ),
    "mw_button_press_topdown": (
        "Use a private z-slide button with overhead clearance and preserve the "
        "source absolute z-axis error metric and 0.024 m threshold."
    ),
    "mw_handle_press": (
        "Use a private vertical sliding handle and preserve the source absolute "
        "z-axis error metric and 0.02 m threshold."
    ),
    "mw_door_open": (
        "Use a private hinged door with an accessible contact face. Preserve the "
        "source absolute door-handle x-axis error and 0.08 m threshold; require "
        "contact-driven hinge motion."
    ),
    "mw_door_close": (
        "Use a private hinged door and preserve the source Euclidean "
        "door-object-to-target metric and 0.08 m threshold."
    ),
    "mw_faucet_open": (
        "Use a private rotary faucet with a collidable radial feature and "
        "preserve the source Euclidean handle-to-target metric and 0.07 m "
        "threshold."
    ),
    "mw_dial_turn": (
        "Use a private dial with a collidable radial feature and preserve the "
        "source Euclidean dial-tip-to-target metric and 0.07 m threshold."
    ),
    "mw_lever_pull": (
        "Use a private hinged lever and preserve the exact source angular error "
        "bound pi/24 radians, encoded as 0.1308996939 rad."
    ),
    "mw_door_lock": (
        "Use a private constrained lock feature with a reachable pressing face. "
        "Preserve the source absolute lock z-axis target error and 0.02 m "
        "threshold; require contact-driven lock motion and do not substitute "
        "door motion."
    ),
    "mw_door_unlock": (
        "Use a private constrained lock feature with a reachable pressing face. "
        "Preserve the source absolute lock x-axis target error and 0.02 m "
        "threshold; require contact-driven lock motion and do not substitute "
        "door motion."
    ),
    "mw_faucet_close": (
        "Use a private rotary faucet with a collidable radial feature. Preserve "
        "the source Euclidean handle-to-target metric and 0.07 m threshold; "
        "fixture geometry may change but the target obligation may not."
    ),
    "mw_soccer": (
        "Use a private free rolling ball and physical goal fixture. Preserve the "
        "unweighted Euclidean ball-centre-to-goal metric returned for success and "
        "the 0.07 m threshold; do not substitute the x-scaled reward-shaping "
        "distance."
    ),
    "mw_window_open": (
        "Use a private constrained sliding window whose opposite handle face is "
        "reachable. Preserve the source absolute handle x-axis target error and "
        "0.05 m threshold; require contact-driven positive-axis slide motion."
    ),
    "mw_window_close": (
        "Use a private constrained sliding window with a reachable closing face. "
        "Preserve the source absolute handle x-axis target error and 0.05 m "
        "threshold; require contact-driven closing-axis slide motion."
    ),
}

EXPECTED_SCORING = {
    "mw_reach_target": (
        "terminal_endpoint",
        "end_effector_target_distance",
        "m",
        "<=",
        0.05,
        "terminal_state",
        "single_trial",
        "metaworld/envs/sawyer_reach_v3.py, evaluate_state lines 82-98 and compute_reward lines 140-162 at pinned commit: tcp_to_target is the Euclidean attachment-point-to-target norm and success is reach_dist <= 0.05.",
    ),
    "mw_push_to_goal": (
        "object_goal_distance",
        "object_goal_distance",
        "m",
        "<=",
        0.05,
        "terminal_state_after_push",
        "single_trial",
        "metaworld/envs/sawyer_push_v3.py, TARGET_RADIUS line 31, evaluate_state lines 85-114, and compute_reward line 179 at pinned commit: target_to_obj is Euclidean object-to-target distance and success is target_to_obj <= 0.05.",
    ),
    "mw_push_wall": (
        "object_goal_distance",
        "object_goal_distance",
        "m",
        "<=",
        0.07,
        "terminal_state_after_push_route",
        "single_trial",
        "metaworld/envs/sawyer_push_wall_v3.py, evaluate_state lines 89-119 and compute_reward line 193 at pinned commit: obj_to_target is Euclidean object-to-target distance and success is obj_to_target <= 0.07.",
    ),
    "mw_sweep_into_goal": (
        "swept_object_distance",
        "object_goal_distance",
        "m",
        "<=",
        0.05,
        "terminal_state_after_sweep",
        "single_trial",
        "metaworld/envs/sawyer_sweep_into_goal_v3.py, model_name lines 63-66, evaluate_state lines 68-92, and compute_reward line 230 at pinned commit: the source uses sawyer_table_with_hole.xml, the metric is Euclidean object-to-target distance, and success is <= 0.05.",
    ),
    "mw_drawer_open": (
        "open_handle_error",
        "drawer_handle_target_distance",
        "m",
        "<=",
        0.03,
        "terminal_state_after_pull",
        "single_trial",
        "metaworld/envs/sawyer_drawer_open_v3.py, evaluate_state lines 66-88 and compute_reward line 125 at pinned commit: handle_error is Euclidean handle-to-target distance and success is handle_error <= 0.03.",
    ),
    "mw_drawer_close": (
        "closed_drawer_distance",
        "drawer_target_distance",
        "m",
        "<=",
        0.065,
        "terminal_state_after_close",
        "single_trial",
        "metaworld/envs/sawyer_drawer_close_v3.py, evaluate_state lines 68-90 and compute_reward lines 132-133 at pinned commit: target_to_obj is Euclidean distance and success is target_to_obj <= self.TARGET_RADIUS + 0.015.",
    ),
    "mw_button_press": (
        "pressed_button_distance",
        "button_target_distance",
        "m",
        "<=",
        0.02,
        "terminal_state_after_contact",
        "single_trial",
        "metaworld/envs/sawyer_button_press_v3.py, evaluate_state lines 61-83 and compute_reward line 139 at pinned commit: obj_to_target is absolute y-axis error and success is obj_to_target <= 0.02.",
    ),
    "mw_button_press_topdown": (
        "topdown_button_distance",
        "button_target_distance",
        "m",
        "<=",
        0.024,
        "terminal_state_after_topdown_contact",
        "single_trial",
        "metaworld/envs/sawyer_button_press_topdown_v3.py, evaluate_state lines 62-83 and compute_reward line 135 at pinned commit: obj_to_target is absolute z-axis error and success is obj_to_target <= 0.024.",
    ),
    "mw_handle_press": (
        "handle_target_distance",
        "handle_target_distance",
        "m",
        "<=",
        0.02,
        "terminal_state_after_press",
        "single_trial",
        "metaworld/envs/sawyer_handle_press_v3.py, TARGET_RADIUS line 16, evaluate_state lines 65-87, and compute_reward lines 133-134 at pinned commit: target_to_obj is absolute z-axis error and success is <= 0.02.",
    ),
    "mw_door_open": (
        "door_open_axis_error",
        "door_x_axis_error",
        "m",
        "<=",
        0.08,
        "terminal_state_after_open",
        "single_trial",
        "metaworld/envs/sawyer_door_v3.py, evaluate_state lines 68-91, especially line 79 at pinned commit: success is abs(obs[4] - self._target_pos[0]) <= 0.08.",
    ),
    "mw_door_close": (
        "closed_door_distance",
        "door_target_distance",
        "m",
        "<=",
        0.08,
        "terminal_state_after_close",
        "single_trial",
        "metaworld/envs/sawyer_door_close_v3.py, evaluate_state lines 105-118 and compute_reward lines 120-134 at pinned commit: obj_to_target is Euclidean distance and success is obj_to_target <= 0.08.",
    ),
    "mw_faucet_open": (
        "faucet_target_distance",
        "faucet_target_distance",
        "m",
        "<=",
        0.07,
        "terminal_state_after_rotation",
        "single_trial",
        "metaworld/envs/sawyer_faucet_open_v3.py, evaluate_state lines 63-85 and compute_reward lines 137-138 at pinned commit: target_to_obj is Euclidean handle-to-target distance and success is <= 0.07.",
    ),
    "mw_dial_turn": (
        "dial_target_distance",
        "dial_target_distance",
        "m",
        "<=",
        0.07,
        "terminal_state_after_turn",
        "single_trial",
        "metaworld/envs/sawyer_dial_turn_v3.py, TARGET_RADIUS line 16, evaluate_state lines 63-85, and compute_reward lines 134-135 at pinned commit: target_to_obj is Euclidean dial-tip-to-target distance and success is target_to_obj <= 0.07.",
    ),
    "mw_lever_pull": (
        "lever_angle_error",
        "lever_angle_error",
        "rad",
        "<=",
        0.1308996939,
        "terminal_state_after_pull",
        "single_trial",
        "metaworld/envs/sawyer_lever_pull_v3.py, evaluate_state lines 80-101 and compute_reward line 168 at pinned commit: lever_error is abs(current joint angle - pi/2) and success is lever_error <= np.pi / 24.",
    ),
    "mw_door_lock": (
        "door_lock_axis_error",
        "door_lock_z_axis_error",
        "m",
        "<=",
        0.02,
        "terminal_state_after_lock_press",
        "single_trial",
        "metaworld/envs/sawyer_door_lock_v3.py, evaluate_state lines 65-87 and compute_reward line 138 at pinned commit: obj_to_target is abs(target_z - lock_z) and success is obj_to_target <= 0.02.",
    ),
    "mw_door_unlock": (
        "door_unlock_axis_error",
        "door_unlock_x_axis_error",
        "m",
        "<=",
        0.02,
        "terminal_state_after_unlock_press",
        "single_trial",
        "metaworld/envs/sawyer_door_unlock_v3.py, evaluate_state lines 63-85 and compute_reward line 154 at pinned commit: obj_to_target is abs(target_x - lock_x) and success is obj_to_target <= 0.02.",
    ),
    "mw_faucet_close": (
        "faucet_close_target_distance",
        "faucet_target_distance",
        "m",
        "<=",
        0.07,
        "terminal_state_after_rotation",
        "single_trial",
        "metaworld/envs/sawyer_faucet_close_v3.py, evaluate_state lines 64-86 and compute_reward lines 138-139 at pinned commit: target_to_obj is Euclidean handle-to-target distance and success is target_to_obj <= 0.07.",
    ),
    "mw_soccer": (
        "soccer_ball_goal_distance",
        "ball_goal_distance",
        "m",
        "<=",
        0.07,
        "terminal_state_after_ball_contact",
        "single_trial",
        "metaworld/envs/sawyer_soccer_v3.py, evaluate_state lines 69-100 and compute_reward lines 227-267, especially line 264, at pinned commit: evaluate_state receives the unweighted Euclidean ball-to-target norm and success is <= 0.07; line 236's x-scaled distance is reward shaping only. metaworld/assets/sawyer_xyz/sawyer_soccer.xml lines 12-23 define the free ball and goal fixture.",
    ),
    "mw_window_open": (
        "window_open_axis_error",
        "window_handle_x_axis_error",
        "m",
        "<=",
        0.05,
        "terminal_state_after_open",
        "single_trial",
        "metaworld/envs/sawyer_window_open_v3.py, TARGET_RADIUS line 27, evaluate_state lines 79-101, and compute_reward lines 136-137 at pinned commit: target_to_obj is absolute handle x-axis error and success is <= 0.05.",
    ),
    "mw_window_close": (
        "window_close_axis_error",
        "window_handle_x_axis_error",
        "m",
        "<=",
        0.05,
        "terminal_state_after_close",
        "single_trial",
        "metaworld/envs/sawyer_window_close_v3.py, TARGET_RADIUS line 28, evaluate_state lines 83-105, and compute_reward lines 143-144 at pinned commit: target_to_obj is absolute handle x-axis error and success is <= 0.05.",
    ),
}


class KukaIiwa14PublicTaskLibraryTests(unittest.TestCase):
    def _load_and_validate(self) -> tuple[dict, dict, tuple[dict, ...]]:
        sources_path = TASKS_ROOT / "sources.json"
        catalog_path = TASKS_ROOT / "catalog.json"
        sources_document = _read_json(sources_path)
        catalog_document = _read_json(catalog_path)
        sources = _validate_sources(sources_document, path=sources_path)
        tasks = _validate_tasks(
            catalog_document,
            path=catalog_path,
            source_ids={str(source["source_id"]) for source in sources},
        )
        return sources_document, catalog_document, tasks

    def test_validators_and_snapshot_identity(self) -> None:
        sources_document, catalog, tasks = self._load_and_validate()
        self.assertEqual(sources_document, EXPECTED_SOURCE_DOCUMENT)

        self.assertEqual(catalog["robot_configuration_id"], "kuka_iiwa_14")
        self.assertEqual(catalog["package_version"], "1.0.0")
        self.assertEqual(catalog["snapshot_id"], SNAPSHOT_ID)
        self.assertEqual(
            catalog["request_envelope"],
            _read_json(FRANKA_CATALOG_PATH)["request_envelope"],
        )

        task_ids = [task["task_id"] for task in tasks]
        self.assertEqual(task_ids, EXPECTED_TASK_IDS)
        self.assertEqual(len(task_ids), 20)
        self.assertEqual(len(set(task_ids)), 20)

    def test_scoring_tuples_and_source_refs_are_exact(self) -> None:
        _, _, tasks = self._load_and_validate()
        self.assertEqual(set(EXPECTED_SCORING), set(EXPECTED_TASK_IDS))
        for task in tasks:
            expected = EXPECTED_SCORING[task["task_id"]]
            clause = task["scoring"][0]
            actual_tuple = (
                clause["clause_id"],
                clause["metric"],
                clause["unit"],
                clause["comparator"],
                clause["threshold"],
                clause["temporal"]["kind"],
                clause["aggregation"]["kind"],
            )
            self.assertEqual(actual_tuple, expected[:7], task["task_id"])

            primary_ref = {
                "source_id": "metaworld_repo",
                "specific_reference": expected[7],
                "support": "adapted",
                "adaptation": task["adaptation"],
            }
            expected_refs = [primary_ref]
            if task["task_id"] == "mw_drawer_close":
                expected_refs.append(INHERITED_DRAWER_REF)
            expected_refs.append(COMMON_EVALUATION_REF)
            self.assertEqual(clause["source_refs"], expected_refs, task["task_id"])

    def test_applicability_adaptation_and_invocation_contracts(self) -> None:
        _, _, tasks = self._load_and_validate()
        for task in tasks:
            task_id = task["task_id"]
            self.assertEqual(task["applicability"], f"{P} {APPLICABILITY_SUFFIXES[task_id]}")
            self.assertEqual(task["adaptation"], EXPECTED_ADAPTATIONS[task_id])

            invocation = task["invocation_schema"]
            self.assertEqual(
                set(invocation["request"]["task_parameters"]["properties"]),
                {
                    "start_position",
                    "contact_position",
                    "target_position",
                    "target_angle",
                    "tool_target_position",
                    "route_position",
                },
            )
            parameters = invocation["request"]["task_parameters"]
            self.assertEqual(parameters["required"], REQUIRED_PARAMETERS[task_id])
            self.assertEqual(parameters["properties"], COMMON_PROPERTIES)
            self.assertFalse(parameters["additional_properties"])

        for property_name in (
            "contact_position",
            "route_position",
            "tool_target_position",
        ):
            description = COMMON_PROPERTIES[property_name]["description"]
            self.assertIn("link7_contact_geom", description)
            self.assertNotIn("attachment_site", description)
        self.assertIn(
            "attachment_site",
            COMMON_PROPERTIES["target_position"]["description"],
        )

    def test_public_task_surface_has_no_status_or_grasp_semantics(self) -> None:
        sources_document, catalog, tasks = self._load_and_validate()
        forbidden_task_keys = {
            "status",
            "classification",
            "admission",
            "direct",
            "conditional",
        }
        for task in tasks:
            self.assertTrue(forbidden_task_keys.isdisjoint(task), task["task_id"])

        public_text = json.dumps(
            {"sources": sources_document, "catalog": catalog},
            sort_keys=True,
        ).lower()
        for forbidden in (
            "grasp_gripper",
            "grasp_wrist_roll",
            "release_position",
            "positive gripper capability",
            "tendon",
            "carrying",
            "retained-grasp",
            "retained grasp",
        ):
            self.assertNotIn(forbidden, public_text)

    def test_kuka_is_not_runnable_and_private_artifacts_are_absent(self) -> None:
        self._load_and_validate()
        runnable_index = _read_json(RUNNABLE_INDEX_PATH)
        self.assertNotIn("kuka_iiwa_14", runnable_index["robots"])

        forbidden_paths = (
            TASKS_ROOT / "private",
            PACKAGE_ROOT / "reference",
            PACKAGE_ROOT / "controller",
            PACKAGE_ROOT / "IK",
            PACKAGE_ROOT / "canary",
        )
        for path in forbidden_paths:
            self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
