"""Thin Stage-1 input adapter around the exact AutoAdapter 1.0 generator."""
from __future__ import annotations

import time
import sys
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_RUNTIME = REPO_ROOT / "demo2" / "legacy_runtime"
LEGACY_CORE = REPO_ROOT / "demo2" / "legacy_core"
for _root in (LEGACY_RUNTIME, LEGACY_CORE):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from auto_adapter.agent import ReactLoop, ReactResult, ToolSpec
from auto_adapter.orchestrator import (
    PhaseResult,
    SelfAssemble,
    _GENERATE_SYSTEM,
)

from .model import OpenAICompatibleConfig, OpenAIReactClient


class Stage1BoundSelfAssemble(SelfAssemble):
    """1.0 SelfAssemble with only the approved Stage-1 input addition."""

    def __init__(self, *args, openai_config: OpenAICompatibleConfig | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.openai_config = openai_config

    def _run_phase(
        self,
        *,
        name: str,
        system: str,
        user_msg: str,
        tools: list[ToolSpec],
        max_iters: int,
        expected_artifacts: list[str],
    ) -> PhaseResult:
        if self.openai_config is None:
            return super()._run_phase(
                name=name,
                system=system,
                user_msg=user_msg,
                tools=tools,
                max_iters=max_iters,
                expected_artifacts=expected_artifacts,
            )
        trace_path = self.workspace / "traces" / f"{name}.jsonl"
        loop = ReactLoop(
            tools=tools,
            system=system,
            model=self.cfg.bedrock_model,
            region=self.cfg.aws_region,
            max_iters=max_iters,
            max_tokens_per_turn=self.cfg.max_tokens_per_turn,
            trace_path=trace_path,
        )
        # Transport-only adaptation: the 1.0 loop, tools, prompts and trace
        # semantics remain unchanged.
        loop.client = OpenAIReactClient(self.openai_config)
        started = time.time()
        result: ReactResult = loop.run(user_msg)
        artifacts: list[Path] = []
        missing: list[str] = []
        for relative in expected_artifacts:
            path = self.workspace / relative
            if path.exists():
                artifacts.append(path)
            else:
                missing.append(relative)
        ok = not missing if expected_artifacts else result.ok
        error = None if ok else (result.error or f"missing expected artifact(s): {missing}")
        return PhaseResult(
            name=name,
            ok=ok,
            duration_sec=time.time() - started,
            trace_path=trace_path,
            artifact_paths=artifacts,
            final_text=result.final_text,
            error=error,
            token_usage=result.total_tokens,
        )

    def _phase_generate(self, prior_validate_failures: Optional[str] = None) -> PhaseResult:
        """Exact 1.0 GENERATE tool bundle plus sealed_design.json."""

        assert self._exec_python_tool is not None
        tools = (
            self._local_tools()
            + self._skeleton_tools()
            + [self._exec_python_tool]
            + self._runtime_tools()
        )
        user_msg = (
            f"Robot ID: {self.cfg.robot_id}\n"
            f"study.json is in the workspace. MJCF is at {self.mjcf_workspace_path}.\n"
            "sealed_design.json is also in the workspace. Read it and implement exactly every "
            "sealed capability effect on the object returned by build(). In particular, if the "
            "chosen 1.0 skeleton calls Cartesian motion move_cartesian but the sealed effect is "
            "move_to_cartesian, expose a real move_to_cartesian method that delegates to it. "
            "Do not read or infer validation cases or thresholds.\n"
            "Produce driver.py per the original 1.0 procedure."
        )
        if prior_validate_failures:
            user_msg += (
                "\n\nIMPORTANT — the previous driver ran in the original direct MuJoCo validator "
                "and produced this complete public failure feedback. Re-generate driver.py, use "
                "the full MJCF/skeleton and local MuJoCo probes to correct it, and preserve every "
                "sealed capability:\n"
                f"{prior_validate_failures}\n"
                "Read validate_report.json for the complete report and verify the fix before returning."
            )
        return self._run_phase(
            name="02_generate",
            system=_GENERATE_SYSTEM,
            user_msg=user_msg,
            tools=tools,
            max_iters=self.cfg.max_iters_generate,
            expected_artifacts=["driver.py"],
        )
