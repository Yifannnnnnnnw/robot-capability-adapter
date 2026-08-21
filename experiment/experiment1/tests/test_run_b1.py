from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch
from pathlib import Path
from types import SimpleNamespace
from typing import Any


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
AUTOADAPTER_SOURCE_ROOT = REPOSITORY_ROOT / "autoadapter" / "src"
for path in (REPOSITORY_ROOT, AUTOADAPTER_SOURCE_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from experiment.experiment1.runtime.b1 import (  # noqa: E402
    B1RunError,
    FixedBundle,
    RecordingClient,
    RunnerHooks,
    _validated_runtime_model_config,
    run_single_cell,
)
from autoadapter2.model_api import JsonModelClient, ModelConfig  # noqa: E402


UNIT_ID = "b1::robotstudio_so101::M5::r01::skeleton-assisted"
FORBIDDEN_STAGES = {"tgcd", "ivc", "task_demo", "hlc", "evolution"}


class ScriptedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.config = SimpleNamespace(
            provider="scripted",
            model="scripted-model",
            api_protocol="scripted",
            base_url="local",
            thinking=None,
            tool_history_mode="native",
            max_tokens=4096,
            timeout_s=30.0,
        )

    def generate_json(self, **_: Any) -> dict[str, Any]:
        return {}


class ScriptedRoute:
    def __init__(self, root: Path, verdicts: list[bool]) -> None:
        self.root = root
        self.verdicts = list(verdicts)
        self.study_calls = 0
        self.generate_calls = 0
        self.repair_calls = 0
        self.harness_calls = 0
        self.client_creations = 0

    def resolver(self, _: Path) -> dict[str, Any]:
        return {
            "experiment_id": "scripted-b1",
            "authority_revision": "test",
            "protocol_id": "b1-driver-synthesis",
            "protocol_version": "test",
            "unit_count": 210,
            "robot_ids": [
                "robotstudio_so101",
                "unitree-go2-stock-12dof",
                "leap_hand",
                "hello_robot_stretch_2",
                "aloha_2",
            ],
            "units": [
                {
                    "unit_id": UNIT_ID,
                    "condition_pair_id": "b1::robotstudio_so101::M5::r01",
                    "robot_configuration_id": "robotstudio_so101",
                    "backbone_id": "M5",
                    "replicate_id": "r01",
                    "replicate_seed": 583329363,
                    "generation_condition": "skeleton-assisted",
                }
            ],
        }

    def bundle(self, *_: Any) -> FixedBundle:
        design_path = self.root / "capability_design.json"
        suite_path = self.root / "capability_validation_suite.json"
        container_path = self.root / "fixed.json"
        for path in (design_path, suite_path, container_path):
            path.write_text("{}\n", encoding="utf-8")
        return FixedBundle(
            design={"capabilities": [{"method_name": "act"}]},
            suite={"cases": [{"case_id": "hidden"}]},
            container_path=container_path,
            design_path=design_path,
            suite_path=suite_path,
            fixed_capability_interface_id="interface-v1",
            fixed_capability_pass_standard_id="criteria-v1",
            validation_suite_id="suite-v1",
        )

    def client(self, _: dict[str, Any]) -> ScriptedClient:
        self.client_creations += 1
        return ScriptedClient()

    def study(self, client: Any, *_: Any, **__: Any) -> Any:
        self.study_calls += 1
        client.generate_json(stage="study", prompt="study", inputs={})
        return SimpleNamespace(
            output={"findings": ["fact"], "implementation_plan": ["plan"]},
            probe_requests=(),
            probe_results=(),
            call_evidence=SimpleNamespace(stage="study", react_trace=()),
        )

    def _candidate(self, client: Any, workspace: Path, stage: str, attempt: int) -> Any:
        client.generate_json(stage=stage, prompt=stage, inputs={})
        workspace.mkdir(parents=True, exist_ok=True)
        driver = workspace / "driver.py"
        source = (
            "class Driver:\n"
            "    def act(self, request):\n"
            "        return None\n"
            "def build():\n"
            "    return Driver()\n"
        )
        driver.write_text(source, encoding="utf-8")
        return SimpleNamespace(
            driver_source=source,
            driver_path=driver,
            source_audit={"passed": True},
            probe_results=(),
            call_evidence=SimpleNamespace(stage=stage, react_trace=()),
            attempt=attempt,
        )

    def generate(self, client: Any, *_: Any, **kwargs: Any) -> Any:
        self.generate_calls += 1
        return self._candidate(client, Path(kwargs["workspace"]), "generate", 0)

    def repair(self, client: Any, **kwargs: Any) -> Any:
        self.repair_calls += 1
        attempt = int(kwargs["previous_attempt"]) + 1
        return self._candidate(client, Path(kwargs["workspace"]), "repair", attempt)

    def harness(self, **_: Any) -> dict[str, Any]:
        self.harness_calls += 1
        verdict = self.verdicts.pop(0)
        return {
            "pipeline_completed": True,
            "physical_validation_executed": True,
            "validation_passed": verdict,
            "video_complete": True,
            "video_manifest": [],
            "trials": [{"trial_passed": verdict}],
        }

    def hooks(self) -> RunnerHooks:
        return RunnerHooks(
            manifest_resolver=self.resolver,
            package_loader=lambda *_: SimpleNamespace(robot_configuration_id="robotstudio_so101"),
            bundle_loader=self.bundle,
            client_factory=self.client,
            study_runner=self.study,
            probe_runner=lambda *_args, **_kwargs: (),
            generate_runner=self.generate,
            public_inputs_builder=lambda *_args, **_kwargs: {"public": True},
            repair_runner=self.repair,
            harness_runner=self.harness,
        )


class Experiment1B1RunnerTests(unittest.TestCase):
    def _run(self, verdicts: list[bool]) -> tuple[dict[str, Any], ScriptedRoute, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manifest = root / "manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        route = ScriptedRoute(root, verdicts)
        output = root / "run"
        record = run_single_cell(
            manifest_path=manifest,
            unit_id=UNIT_ID,
            output_dir=output,
            hooks=route.hooks(),
        )
        return record, route, output

    def test_attempt_zero_pass_stops_without_repair(self) -> None:
        record, route, output = self._run([True])

        self.assertTrue(record["terminal_verdict"]["validation_passed"])
        self.assertEqual(record["derived"]["submitted_attempt_count"], 1)
        self.assertEqual(len(record["attempts"]), 1)
        self.assertEqual(route.client_creations, 1)
        self.assertEqual(route.generate_calls, 1)
        self.assertEqual(route.repair_calls, 0)
        self.assertEqual(route.harness_calls, 1)
        self.assertEqual(
            [stage["stage"] for stage in record["stages"]],
            ["study", "generate", "validation"],
        )
        self.assertEqual(len(record["provider_calls"]), 2)
        self.assertTrue((output / "cell_record.json").is_file())

    def test_failed_attempt_zero_repairs_once_then_passes(self) -> None:
        record, route, _ = self._run([False, True])

        self.assertTrue(record["terminal_verdict"]["validation_passed"])
        self.assertEqual(record["derived"]["submitted_attempt_count"], 2)
        self.assertEqual(
            [attempt["attempt_index"] for attempt in record["attempts"]], [0, 1]
        )
        self.assertEqual(
            [attempt["validation_verdict"] for attempt in record["attempts"]],
            [False, True],
        )
        self.assertEqual(route.repair_calls, 1)
        self.assertEqual(route.harness_calls, 2)
        self.assertEqual(record["attempts"][0]["transition_or_stop"], "repair_1")
        self.assertEqual(record["attempts"][1]["transition_or_stop"], "passed")

    def test_three_failures_stop_at_the_accepted_attempt_budget(self) -> None:
        record, route, _ = self._run([False, False, False])

        self.assertFalse(record["terminal_verdict"]["validation_passed"])
        self.assertEqual(
            record["terminal_verdict"]["stop_reason"], "maximum_attempts_reached"
        )
        self.assertEqual(record["derived"]["submitted_attempt_count"], 3)
        self.assertEqual(len(record["attempts"]), 3)
        self.assertEqual(route.generate_calls, 1)
        self.assertEqual(route.repair_calls, 2)
        self.assertEqual(route.harness_calls, 3)

    def test_route_contains_no_forbidden_stage_or_provider_call(self) -> None:
        record, _, _ = self._run([False, True])

        stages = {str(stage["stage"]) for stage in record["stages"]}
        provider_stages = {
            str(call["stage"]) for call in record["provider_calls"]
        }
        self.assertTrue(FORBIDDEN_STAGES.isdisjoint(stages))
        self.assertTrue(FORBIDDEN_STAGES.isdisjoint(provider_stages))
        self.assertEqual(record["model"]["provider_seed_applied"], None)
        self.assertEqual(record["identity"]["unit_id"], UNIT_ID)
        self.assertEqual(record["identity"]["backbone_id"], "M5")
        self.assertEqual(record["identity"]["replicate_id"], "r01")

    def test_default_bundle_loader_refuses_a_missing_criteria_path(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps({"fixed_validation_bundle_set": None}), encoding="utf-8"
        )
        route = ScriptedRoute(root, [True])
        hooks = RunnerHooks(
            manifest_resolver=route.resolver,
            package_loader=lambda *_: SimpleNamespace(robot_configuration_id="robotstudio_so101"),
            client_factory=route.client,
            study_runner=route.study,
            generate_runner=route.generate,
            public_inputs_builder=lambda *_args, **_kwargs: {"public": True},
            repair_runner=route.repair,
            harness_runner=route.harness,
        )

        with self.assertRaisesRegex(B1RunError, "Driver-and-criteria path is missing"):
            run_single_cell(
                manifest_path=manifest,
                unit_id=UNIT_ID,
                output_dir=root / "run",
                hooks=hooks,
            )
        self.assertEqual(route.client_creations, 0)

    def test_pinned_runtime_config_and_provider_cost_are_observed(self) -> None:
        pinned = json.loads(
            (
                EXPERIMENT_ROOT / "providers" / "M5-deepseek-v4-pro.json"
            ).read_text(encoding="utf-8")
        )
        environment = {
            "AUTOADAPTER_MODEL_PROVIDER": "openai-compatible",
            "AUTOADAPTER_MODEL_ID": "deepseek-v4-pro",
            "AUTOADAPTER_MODEL_API_BASE_URL": "https://api.deepseek.com",
            "AUTOADAPTER_MODEL_API_KEY": "test-only",
            "AUTOADAPTER_MODEL_THINKING": "disabled",
            "AUTOADAPTER_MODEL_MAX_TOKENS": "16384",
            "AUTOADAPTER_MODEL_TIMEOUT_S": "180",
            "AUTOADAPTER_MODEL_TOOL_HISTORY_MODE": "native",
            "AUTOADAPTER_MODEL_HISTORY_CHARS": "80000",
        }
        with patch.dict("os.environ", environment, clear=True):
            config = _validated_runtime_model_config(pinned)
        self.assertEqual(config.model, "deepseek-v4-pro")

        class UsageClient(ScriptedClient):
            def generate_json(self, **_: Any) -> dict[str, Any]:
                self.calls.append(
                    {
                        "requested_model": "deepseek-v4-pro",
                        "returned_model": "deepseek-v4-pro",
                        "usage": {
                            "prompt_cache_hit_tokens": 1_000_000,
                            "prompt_cache_miss_tokens": 2_000_000,
                            "completion_tokens": 3_000_000,
                        },
                    }
                )
                return {}

        recorder = RecordingClient(
            UsageClient(), lambda: None, pinned["price_snapshot"]
        )
        recorder.generate_json(stage="study", prompt="study", inputs={})
        self.assertAlmostEqual(
            recorder.calls[0]["per_call_cost"],
            0.003625 + (2 * 0.435) + (3 * 0.87),
        )
        self.assertEqual(
            recorder.calls[0]["tokens"]["input_cache_miss_tokens"], 2_000_000
        )

    def test_retry_requests_remain_separate_with_one_completed_model_turn(self) -> None:
        client = JsonModelClient(
            ModelConfig(
                provider="company",
                model="model",
                base_url="https://model.example/v1",
                api_key="secret-value",
            )
        )
        headers = Message()
        headers["x-request-id"] = "req-429"
        error = urllib.error.HTTPError(
            client.config.endpoint_url,
            429,
            "Too Many Requests",
            headers,
            None,
        )
        payload = {
            "id": "req-success",
            "model": "model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}"},
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 1,
                "total_tokens": 5,
            },
        }
        response = MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.headers = {}
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        updates: list[bool] = []
        recorder = RecordingClient(client, lambda: updates.append(True))

        with patch.object(client, "_retry_pause"), patch(
            "urllib.request.urlopen", side_effect=[error, response]
        ):
            self.assertEqual(
                recorder.generate_json(stage="study", prompt="study", inputs={}),
                {},
            )

        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(len(updates), 2)
        failed, succeeded = recorder.calls
        self.assertEqual(failed["call_index"], 1)
        self.assertIsNone(failed["model_turn_index"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["http_status"], 429)
        self.assertEqual(failed["retry_index"], 0)
        self.assertIsNone(failed["retry_of"])
        self.assertEqual(succeeded["call_index"], 2)
        self.assertEqual(succeeded["model_turn_index"], 1)
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(succeeded["http_status"], 200)
        self.assertEqual(succeeded["retry_index"], 1)
        self.assertEqual(succeeded["retry_of"], 1)
        self.assertIsNone(failed["tokens"]["input_tokens"])
        self.assertEqual(succeeded["tokens"]["input_tokens"], 4)


if __name__ == "__main__":
    unittest.main()
