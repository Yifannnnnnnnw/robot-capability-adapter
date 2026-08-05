"""Independent read-only Evolution Agent orchestration.

All mutation authority is outside the Agent: the trusted functions in
``evolution.py`` recompile evidence, judge one claim, and conditionally append
one Experience record.  This module supplies only bounded reads, identity,
budget, trace, and run reporting.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .audit import (
    BudgetCounter,
    BudgetExceeded,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from .evolution import (
    EVOLUTION_ARCHITECTURE,
    build_evolution_projection,
    evaluate_and_publish_evolution_audit,
)
from .libraries import load_structured
from .model_client import AWSModelAPIClient, AWSModelAPIConfig, ModelClient
from .react_agent import ReactAgent, ToolSpec
from .schema_validation import validate_json_schema


class EvolutionRuntimeError(RuntimeError):
    """The read-only Evolution orchestration contract was violated."""


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9_./:()\[\]{} -]{1,120}$")
_SECRET_RE = re.compile(r"\brp_[A-Za-z0-9]{16,}\b")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?:^|[\s=\"'])(?:/(?:users|home|private|var|tmp)/|[a-z]:\\users\\)"
)
_TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".jsonl"}
_MAX_PUBLIC_FILE_BYTES = 256 * 1024
_MAX_READ_LINES = 200
_MAX_READ_BYTES = 16 * 1024
_MAX_AUDIT_BYTES = 64 * 1024
_PUBLIC_TOP_LEVEL_FILES = {
    "README.md",
    "DEMO_PLAN.md",
    "SOARM101_MINIMAL_DEMO_PLAN.md",
    "EVOLUTION_GOAL.md",
    "EVOLUTION_DESIGN.md",
    "EVOLUTION_CORRECTION_RECEIPT.json",
    "RESULTS.md",
}
_PUBLIC_ROOTS = {
    "prompts",
    "schemas",
    "configs",
    "src",
    "libraries",
}
_FORBIDDEN_COMPONENTS = {
    ".git",
    ".venv",
    "__pycache__",
    "runs",
    "private",
    "tests",
    "fixtures",
    "splits",
}
_FORBIDDEN_PATH_TOKENS = (
    ".env",
    "heldout",
    "oracle",
    "records.jsonl",
    "candidate_bundle.json",
)
_FORBIDDEN_CONTENT_TOKENS = (
    ".env",
    "heldout_tasks.jsonl",
    "task_oracles.yaml",
    "private/task_library",
    "private_task_oracles",
    "private_demo_batch",
    "private_initial_states",
    "demo_batch.json",
    "soarm101_p0_push_cylinder_lateral",
    "soarm101_p0_place_cube_in_bowl_new_region",
    "soarm101_p0_sort_two_cubes_matching_trays",
)
_REDACTED_PUBLIC_LINE = "[REDACTED_PRIVATE_REFERENCE]"
_PRIVATE_SOURCE_BLOCK_STARTS = {
    "src/soarm_demo/pipeline.py": ("def offline_demo_responses(",),
}


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _has_symlink_component(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _public_path_allowed(relative: Path) -> bool:
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return False
    if any(part in _FORBIDDEN_COMPONENTS for part in relative.parts):
        return False
    lowered = relative.as_posix().lower()
    if any(token in lowered for token in _FORBIDDEN_PATH_TOKENS):
        return False
    if len(relative.parts) == 1:
        return relative.name in _PUBLIC_TOP_LEVEL_FILES
    if relative.parts[0] not in _PUBLIC_ROOTS:
        return False
    if relative.parts[0] == "src" and relative.parts[1:2] != ("soarm_demo",):
        return False
    if relative.parts[0] == "libraries":
        # Public inputs only. Raw Experience provenance stays framework-side.
        if len(relative.parts) < 2 or relative.parts[1] not in {
            "morphology",
            "sdk_runtime",
            "tasks",
        }:
            return False
    return relative.suffix.lower() in _TEXT_SUFFIXES


def _sanitize_public_repository_text(
    text: str,
    *,
    relative_path: str | None = None,
) -> tuple[str, int]:
    output: list[str] = []
    redacted = 0
    redacting_block = False
    block_starts = _PRIVATE_SOURCE_BLOCK_STARTS.get(relative_path or "", ())
    for line in text.splitlines():
        if any(line.startswith(marker) for marker in block_starts):
            redacting_block = True
        elif redacting_block and line.startswith(("def ", "class ")):
            redacting_block = False
        lowered = line.lower()
        if (
            redacting_block
            or _SECRET_RE.search(line)
            or _ABSOLUTE_PATH_RE.search(line)
            or any(token in lowered for token in _FORBIDDEN_CONTENT_TOKENS)
        ):
            output.append(_REDACTED_PUBLIC_LINE)
            redacted += 1
        else:
            output.append(line)
    return "\n".join(output), redacted


def _source_run_public_hashes(source_run: Path | None) -> dict[str, str]:
    if source_run is None:
        return {}
    path = source_run / "run_inputs.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    fixed_files = document.get("fixed_files") if isinstance(document, Mapping) else None
    if not isinstance(fixed_files, list):
        return {}
    return {
        item["path"]: item["sha256"]
        for item in fixed_files
        if isinstance(item, Mapping)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
    }


def _build_public_repository_index(
    demo_root: Path,
    *,
    source_run: Path | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    source_hashes = _source_run_public_hashes(source_run)
    for path in sorted(demo_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(demo_root)
        except ValueError:
            continue
        if not _public_path_allowed(relative):
            continue
        try:
            size = path.stat().st_size
            if size > _MAX_PUBLIC_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        public_text, redacted_lines = _sanitize_public_repository_text(
            text,
            relative_path=relative.as_posix(),
        )
        digest = sha256_file(path)
        source_digest = source_hashes.get(relative.as_posix())
        source_binding = (
            "not_checked"
            if source_run is None
            else "not_recorded_in_source_run"
            if source_digest is None
            else "matched_source_run"
            if source_digest == digest
            else "changed_since_source_run"
        )
        files.append(
            {
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": digest,
                "public_view_sha256": sha256_bytes(public_text.encode("utf-8")),
                "redacted_line_count": redacted_lines,
                "source_run_binding": source_binding,
            }
        )
    binding_counts: dict[str, int] = {}
    for item in files:
        status = item["source_run_binding"]
        binding_counts[status] = binding_counts.get(status, 0) + 1
    return {
        "schema_version": "robot_capability.evolution_framework_inputs.v1",
        "files": files,
        "tree_sha256": sha256_json(files),
        "source_run_binding_counts": dict(sorted(binding_counts.items())),
        "agent_permissions": {
            "read_public_projection": True,
            "read_public_repository": True,
            "write_repository": False,
            "write_source_run": False,
            "shell": False,
            "network_except_model_client": False,
        },
    }


class _AuditWorkspace:
    def __init__(
        self,
        *,
        demo_root: Path,
        projection: Mapping[str, Any],
        framework_inputs: Mapping[str, Any],
        audit_schema: Mapping[str, Any],
    ) -> None:
        self.demo_root = demo_root
        self.projection = dict(projection)
        self.audit_schema = dict(audit_schema)
        self.index = {
            item["path"]: dict(item)
            for item in framework_inputs.get("files", [])
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        }
        self.repository_refs: set[str] = set()
        self.audit: dict[str, Any] | None = None

    def _resolve_public_file(self, relative_text: str) -> tuple[Path, dict[str, Any]]:
        if not isinstance(relative_text, str):
            raise EvolutionRuntimeError("path must be a repository-relative string")
        relative = Path(relative_text)
        if relative_text not in self.index or not _public_path_allowed(relative):
            raise EvolutionRuntimeError("path is not in the frozen public audit surface")
        if _has_symlink_component(self.demo_root, relative):
            raise EvolutionRuntimeError("public path is unsafe")
        target = (self.demo_root / relative).resolve(strict=True)
        if not _is_within(target, self.demo_root):
            raise EvolutionRuntimeError("public path is unsafe")
        expected = self.index[relative_text]
        if sha256_file(target) != expected["sha256"]:
            raise EvolutionRuntimeError("public file changed after audit snapshot")
        return target, expected

    def read_evidence(self, arguments: dict[str, Any]) -> Any:
        section = arguments["section"]
        if section not in self.projection:
            raise EvolutionRuntimeError("unknown evidence section")
        return self.projection[section]

    def list_files(self, arguments: dict[str, Any]) -> Any:
        prefix = arguments.get("prefix", "")
        values = [
            item
            for path, item in sorted(self.index.items())
            if not prefix or path == prefix or path.startswith(prefix.rstrip("/") + "/")
        ]
        return {"files": values[:250], "omitted": max(0, len(values) - 250)}

    def read_file(self, arguments: dict[str, Any]) -> Any:
        path_text = arguments["path"]
        start_line = arguments.get("start_line", 1)
        max_lines = arguments.get("max_lines", _MAX_READ_LINES)
        target, expected = self._resolve_public_file(path_text)
        public_text, _ = _sanitize_public_repository_text(
            target.read_text(encoding="utf-8"),
            relative_path=path_text,
        )
        lines = public_text.splitlines()
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        text = "\n".join(selected)
        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_READ_BYTES:
            encoded = encoded[:_MAX_READ_BYTES]
            text = encoded.decode("utf-8", errors="ignore")
        end_line = start_line + max(0, len(selected) - 1)
        ref = f"repo:{path_text}@{expected['sha256']}#L{start_line}-L{end_line}"
        self.repository_refs.add(ref)
        return {
            "evidence_ref": ref,
            "path": path_text,
            "file_sha256": expected["sha256"],
            "source_run_binding": expected["source_run_binding"],
            "start_line": start_line,
            "end_line": end_line,
            "content": text,
            "truncated_by_byte_limit": len(encoded) >= _MAX_READ_BYTES,
        }

    def search(self, arguments: dict[str, Any]) -> Any:
        query = arguments["query"]
        prefix = arguments.get("prefix", "")
        if not _SAFE_QUERY_RE.fullmatch(query):
            raise EvolutionRuntimeError("search query is outside the public safe grammar")
        lowered_query = query.lower()
        results: list[dict[str, Any]] = []
        for path_text in sorted(self.index):
            if prefix and not (
                path_text == prefix or path_text.startswith(prefix.rstrip("/") + "/")
            ):
                continue
            target, expected = self._resolve_public_file(path_text)
            public_text, _ = _sanitize_public_repository_text(
                target.read_text(encoding="utf-8"),
                relative_path=path_text,
            )
            for number, line in enumerate(public_text.splitlines(), start=1):
                if lowered_query not in line.lower():
                    continue
                ref = f"repo:{path_text}@{expected['sha256']}#L{number}-L{number}"
                self.repository_refs.add(ref)
                results.append(
                    {
                        "evidence_ref": ref,
                        "path": path_text,
                        "source_run_binding": expected["source_run_binding"],
                        "line": number,
                        "text": line[:300],
                    }
                )
                if len(results) >= 30:
                    return {"matches": results, "truncated": True}
        return {"matches": results, "truncated": False}

    def submit(self, arguments: dict[str, Any]) -> Any:
        if self.audit is not None:
            return {"accepted": False, "reason": "audit_already_submitted_and_frozen"}
        audit = arguments["audit"]
        issues = validate_json_schema(audit, self.audit_schema, instance_path="$audit")
        if issues:
            return {
                "accepted": False,
                "reason": "audit_schema_invalid",
                "issues": [
                    {"path": issue.path, "message": issue.message[:400]}
                    for issue in issues[:20]
                ],
            }
        if len(canonical_json(audit).encode("utf-8")) > _MAX_AUDIT_BYTES:
            return {"accepted": False, "reason": "audit_too_large"}
        ranks = [item["rank"] for item in audit["ranked_findings"]]
        ids = [item["finding_id"] for item in audit["ranked_findings"]]
        if ranks != list(range(1, len(ranks) + 1)) or len(ids) != len(set(ids)):
            return {"accepted": False, "reason": "ranking_not_contiguous_or_unique"}
        if audit["selected_finding_id"] not in ids:
            return {"accepted": False, "reason": "selected_finding_missing"}
        if audit["source_run_id"] != self.projection["source"]["run_id"]:
            return {"accepted": False, "reason": "source_run_id_mismatch"}
        known_refs = set(self.projection["evidence_index"]) | self.repository_refs
        used_refs = {
            ref
            for finding in audit["ranked_findings"]
            for ref in finding["evidence_refs"]
        } | set(audit["experience_claim"]["evidence_refs"])
        if not used_refs <= known_refs:
            return {"accepted": False, "reason": "unknown_evidence_ref"}
        selected = next(
            item
            for item in audit["ranked_findings"]
            if item["finding_id"] == audit["selected_finding_id"]
        )
        claim = audit["experience_claim"]
        claim_refs = set(claim["evidence_refs"])
        if not claim_refs & set(self.projection["evidence_index"]):
            return {"accepted": False, "reason": "claim_requires_run_evidence"}
        if not claim_refs <= set(selected["evidence_refs"]):
            return {
                "accepted": False,
                "reason": "claim_refs_not_subset_of_selected_finding",
            }
        candidate_outcomes = {
            self.projection["evidence_index"][ref].get("causal_outcome")
            for ref in claim_refs
            if ref.startswith("candidate:")
            and ref in self.projection["evidence_index"]
        }
        conclusion = claim["conclusion_kind"]
        if conclusion == "negative" and "confirmed_failure" not in candidate_outcomes:
            return {
                "accepted": False,
                "reason": "negative_requires_confirmed_failure_candidate",
            }
        if conclusion == "positive" and "confirmed_success" not in candidate_outcomes:
            return {
                "accepted": False,
                "reason": "positive_requires_confirmed_success_candidate",
            }
        self.audit = dict(audit)
        return {
            "accepted": True,
            "selected_finding_id": audit["selected_finding_id"],
            "note": "The trusted evaluator, not the Agent, determines publication.",
        }


def _read_only_tools(workspace: _AuditWorkspace) -> dict[str, ToolSpec]:
    return {
        "read_evolution_evidence": ToolSpec(
            name="read_evolution_evidence",
            description="Read one privacy-safe, verified evidence section.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["section"],
                "properties": {
                    "section": {
                        "enum": [
                            "source", "collection", "generation", "accounting",
                            "validation", "repair", "demo", "video",
                            "compiler_candidates", "evidence_index",
                            "research_boundaries", "privacy",
                            "submission_contract",
                        ]
                    }
                },
            },
            handler=workspace.read_evidence,
        ),
        "list_public_repository_files": ToolSpec(
            name="list_public_repository_files",
            description="List files in the frozen public, non-test repository surface.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"prefix": {"type": "string", "maxLength": 120}},
            },
            handler=workspace.list_files,
        ),
        "read_public_repository_file": ToolSpec(
            name="read_public_repository_file",
            description="Read at most 200 lines/16 KiB from one frozen public file.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 240},
                    "start_line": {"type": "integer", "minimum": 1, "maximum": 1000000},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            handler=workspace.read_file,
        ),
        "search_public_repository": ToolSpec(
            name="search_public_repository",
            description="Search the frozen public surface without shell access.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 120},
                    "prefix": {"type": "string", "maxLength": 120},
                },
            },
            handler=workspace.search,
        ),
        "submit_evolution_audit": ToolSpec(
            name="submit_evolution_audit",
            description="Submit exactly one ranked audit and one Experience claim.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["audit"],
                "properties": {"audit": workspace.audit_schema},
            },
            handler=workspace.submit,
        ),
    }


def build_global_evidence_projection(
    demo_root: str | Path,
    source_run: str | Path,
) -> dict[str, Any]:
    """Compatibility entry point backed by the single trusted compiler."""

    root = Path(demo_root).resolve()
    runs_path = root / "runs"
    if runs_path.is_symlink():
        raise EvolutionRuntimeError("demo/runs must not be a symlink")
    runs_root = runs_path.resolve()
    if not _is_within(runs_root, root):
        raise EvolutionRuntimeError("demo/runs escaped the Demo root")
    source_input = Path(source_run)
    if source_input.is_symlink():
        raise EvolutionRuntimeError("source run must be a direct, non-symlink child of demo/runs")
    source = source_input.resolve()
    if source.parent != runs_root:
        raise EvolutionRuntimeError("source run must be a direct, non-symlink child of demo/runs")
    return build_evolution_projection(source, schema_root=root / "schemas")


def _load_local_env(path: Path) -> None:
    if not path.is_file():
        return
    allowed = {
        "AWS_MODEL_API_KEY",
        "AWS_MODEL_API_SHORT_ENDPOINT",
        "AWS_MODEL_API_LONG_BASE_URL",
        "AWS_MODEL_API_LONG_PATH",
    }
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in allowed:
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(name, value)


def load_aws_evolution_client(demo_root: str | Path) -> ModelClient:
    """Create one credential-isolated client for the independent Agent."""

    root = Path(demo_root).resolve()
    _load_local_env(root / ".env")
    document = load_structured(root / "configs/models.yaml")
    if not isinstance(document, Mapping):
        raise EvolutionRuntimeError("models.yaml must be an object")
    profile_name = document.get("default_profile")
    profiles = document.get("profiles")
    profile = profiles.get(profile_name) if isinstance(profiles, Mapping) else None
    if not isinstance(profile, Mapping) or profile.get("provider") != "aws_model_api":
        raise EvolutionRuntimeError("default model profile is not AWS Model API")
    config = AWSModelAPIConfig(
        model=str(profile["model"]),
        api_key_env=str(profile["api_key_env"]),
        route=str(profile["route"]),
        short_endpoint=str(profile["short_endpoint"]),
        long_base_url=str(profile["long_base_url"]),
        long_path=str(profile["long_path"]),
        max_tokens=int(profile["max_tokens"]),
        temperature=float(profile["temperature"]),
        short_timeout_s=float(profile["short_timeout_s"]),
        long_timeout_s=float(profile["long_timeout_s"]),
        max_retries=int(profile["max_retries"]),
    )
    if config.max_retries != 0:
        raise EvolutionRuntimeError("Evolution requires one HTTP attempt per Agent turn")
    api_key = os.environ.pop(config.api_key_env, None)
    if not api_key:
        raise EvolutionRuntimeError("complete AWS Model API credential is unavailable")
    return AWSModelAPIClient(config, api_key=api_key)


def _evolution_report_semantic_errors(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    agent = report.get("agent") if isinstance(report.get("agent"), Mapping) else {}
    evaluation = (
        report.get("evaluation")
        if isinstance(report.get("evaluation"), Mapping)
        else {}
    )
    experience = (
        report.get("experience")
        if isinstance(report.get("experience"), Mapping)
        else {}
    )
    guards = (
        report.get("read_only_guards")
        if isinstance(report.get("read_only_guards"), Mapping)
        else {}
    )
    budget = (
        agent.get("agent_turn_budget")
        if isinstance(agent.get("agent_turn_budget"), Mapping)
        else {}
    )
    if budget.get("used") != agent.get("agent_turns"):
        errors.append("agent_turn_budget.used must equal agent_turns")
    if all(isinstance(budget.get(key), int) for key in ("limit", "used", "remaining")):
        if budget["used"] + budget["remaining"] != budget["limit"]:
            errors.append("agent turn budget must conserve limit")
    phase_policy = (
        agent.get("phase_budget_policy")
        if isinstance(agent.get("phase_budget_policy"), Mapping)
        else {}
    )
    if all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (
            phase_policy.get("global_audit_limit"),
            phase_policy.get("synthesis_limit"),
            budget.get("limit"),
        )
    ) and (
        phase_policy["global_audit_limit"] + phase_policy["synthesis_limit"]
        != budget["limit"]
    ):
        errors.append("phase budgets must equal the total Agent budget")

    gates = evaluation.get("gates") if isinstance(evaluation.get("gates"), Mapping) else {}
    framework_gate = gates.get("framework_snapshot")
    framework_passed = (
        framework_gate.get("passed")
        if isinstance(framework_gate, Mapping)
        else None
    )
    if framework_passed != guards.get("framework_unchanged_during_agent"):
        errors.append("framework gate must equal the read-only guard")
    verdict = evaluation.get("verdict")
    accepted = verdict == "accepted"
    claims = evaluation.get("claims") if isinstance(evaluation.get("claims"), Mapping) else {}
    if evaluation.get("publishable") is not accepted:
        errors.append("publishable must agree with evaluator verdict")
    if claims.get("experience_claim_accepted") is not accepted:
        errors.append("experience_claim_accepted must agree with evaluator verdict")
    if accepted and any(
        not isinstance(gate, Mapping) or gate.get("passed") is not True
        for gate in gates.values()
    ):
        errors.append("an accepted evaluation requires every trusted gate to pass")
    terminal = report.get("terminal_status")
    if terminal == "EVOLUTION_ACCEPTED":
        if not accepted or experience.get("published") is not True:
            errors.append("EVOLUTION_ACCEPTED requires accepted and published")
    elif terminal == "EVOLUTION_REJECTED":
        if verdict != "rejected" or experience.get("published") is not False:
            errors.append("EVOLUTION_REJECTED requires rejected and unpublished")
    elif terminal == "EVOLUTION_INCONCLUSIVE":
        if not (
            (verdict == "inconclusive" and experience.get("published") is False)
            or (accepted and experience.get("published") is False)
        ):
            errors.append("EVOLUTION_INCONCLUSIVE has inconsistent evaluator/publication state")
    if experience.get("published") is True:
        if experience.get("record_sha256") != evaluation.get("approved_record_sha256"):
            errors.append("published record hash must equal evaluator-approved hash")
    return errors


def _validate_evolution_report(
    report: Mapping[str, Any],
    report_schema: Mapping[str, Any],
) -> None:
    schema_issues = validate_json_schema(report, report_schema, instance_path="$report")
    semantic_issues = _evolution_report_semantic_errors(report)
    if schema_issues or semantic_issues:
        raise EvolutionRuntimeError(
            "trusted Evolution report failed schema/semantic validation "
            f"({len(schema_issues)} schema, {len(semantic_issues)} semantic issue(s))"
        )


def run_evolution(
    *,
    demo_root: str | Path,
    source_run: str | Path,
    run_id: str,
    client: ModelClient,
    call_limit: int = 30,
) -> Path:
    """Run one independent audit and conditionally publish one Experience."""

    root = Path(demo_root).resolve()
    runs_path = root / "runs"
    if runs_path.is_symlink():
        raise EvolutionRuntimeError("demo/runs must not be a symlink")
    runs_root = runs_path.resolve()
    if not _is_within(runs_root, root):
        raise EvolutionRuntimeError("demo/runs escaped the Demo root")
    source_input = Path(source_run)
    if source_input.is_symlink():
        raise EvolutionRuntimeError("source run must be a direct, existing child of demo/runs")
    source = source_input.resolve()
    if source.parent != runs_root or not source.is_dir():
        raise EvolutionRuntimeError("source run must be a direct, existing child of demo/runs")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise EvolutionRuntimeError("invalid Evolution run_id")
    output = runs_root / run_id
    if output.exists() or output.is_symlink():
        raise EvolutionRuntimeError("Evolution output run already exists")
    if isinstance(call_limit, bool) or not isinstance(call_limit, int) or not 1 <= call_limit <= 30:
        raise EvolutionRuntimeError("Evolution call_limit must be in [1, 30]")
    evolution_dir = output / "evolution"
    evolution_dir.mkdir(parents=True)

    projection = build_global_evidence_projection(root, source)
    projection_sha = sha256_json(projection)
    framework_inputs = _build_public_repository_index(root, source_run=source)
    framework_sha = framework_inputs["tree_sha256"]
    atomic_write_json(evolution_dir / "evidence_projection.json", projection)
    atomic_write_json(evolution_dir / "framework_inputs.json", framework_inputs)

    audit_schema = load_structured(root / "schemas/evolution_audit.schema.json")
    if not isinstance(audit_schema, Mapping):
        raise EvolutionRuntimeError("evolution_audit.schema.json must be an object")
    workspace = _AuditWorkspace(
        demo_root=root,
        projection=projection,
        framework_inputs=framework_inputs,
        audit_schema=audit_schema,
    )
    system_prompt = (root / "prompts/evolution_system.md").read_text(encoding="utf-8")
    instruction = (root / "prompts/evolution_audit.md").read_text(encoding="utf-8")
    all_tools = _read_only_tools(workspace)
    if call_limit >= 3:
        audit_limit = min(20, call_limit - 2)
    else:
        audit_limit = call_limit
    synthesis_limit = call_limit - audit_limit
    agent = ReactAgent(
        agent_id="soarm101-evolution-global-auditor",
        role="evolution_global_auditor",
        client=client,
        system_prompt=system_prompt,
        tools=all_tools,
        budget=BudgetCounter("evolution_global_audit", audit_limit),
        trace_path=evolution_dir / "agent_trace.jsonl",
    )
    agent.set_phase("global_audit")
    agent_error: str | None = None
    forced_synthesis_transition = False
    final_guard = lambda: (
        None
        if workspace.audit is not None
        else "submit one schema-valid Evolution audit before final"
    )
    try:
        agent_result = agent.run(instruction, final_guard=final_guard)
    except BudgetExceeded:
        if workspace.audit is not None:
            agent_result = agent.accounting_result(
                status="completed",
                content="Audit accepted on the final global-audit turn.",
            )
        elif synthesis_limit > 0:
            forced_synthesis_transition = True
            agent.budget = BudgetCounter("evolution_synthesis", synthesis_limit)
            synthesis_tools = {
                name: all_tools[name]
                for name in (
                    "read_evolution_evidence",
                    "submit_evolution_audit",
                )
            }
            agent.set_phase(
                "synthesize_experience",
                tools=synthesis_tools,
                instruction=(
                    "Global inspection is closed. Use the remaining calls only to "
                    "read compiler_candidates/evidence_index/submission_contract as "
                    "needed, then submit one exact-schema audit. Repository browsing "
                    "and further issue discovery are no longer available."
                ),
            )
            try:
                agent_result = agent.run(
                    "Synthesize the ranked evidence already inspected and submit the "
                    "single Experience claim now. Preserve uncertainty and the exact "
                    "trusted evidence rules.",
                    final_guard=final_guard,
                )
            except BudgetExceeded:
                if workspace.audit is not None:
                    agent_result = agent.accounting_result(
                        status="completed",
                        content="Audit accepted on the final synthesis turn.",
                    )
                else:
                    agent_error = "BudgetExceeded"
                    agent_result = agent.accounting_result(status="failed", content="")
            except Exception as exc:
                agent_error = type(exc).__name__
                agent_result = agent.accounting_result(status="failed", content="")
        else:
            agent_error = "BudgetExceeded"
            agent_result = agent.accounting_result(status="failed", content="")
    except Exception as exc:
        agent_error = type(exc).__name__
        agent_result = agent.accounting_result(status="failed", content="")

    if workspace.audit is not None:
        atomic_write_json(evolution_dir / "audit.json", workspace.audit)

    current_framework = _build_public_repository_index(root, source_run=source)
    framework_unchanged = current_framework["tree_sha256"] == framework_sha
    total_budget = {
        "name": "evolution_total",
        "limit": call_limit,
        "used": agent_result.agent_turns,
        "remaining": max(0, call_limit - agent_result.agent_turns),
    }
    provider_policy = dict(agent_result.provider_attempt_budget_policy)
    provider_policy["agent_window"] = {
        "derivation": "global_audit_window + synthesis_window",
        "limit": call_limit,
        "used": agent_result.provider_http_attempts,
        "remaining": max(0, call_limit - agent_result.provider_http_attempts),
    }
    report_schema = load_structured(root / "schemas/evolution_run_report.schema.json")
    if not isinstance(report_schema, Mapping):
        raise EvolutionRuntimeError("evolution_run_report.schema.json must be an object")

    def build_report(
        evaluation: Mapping[str, Any],
        publication: Mapping[str, Any],
        terminal: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "robot_capability.evolution_run_report.v1",
            "run_id": run_id,
            "source_run_id": projection["source"]["run_id"],
            "architecture": EVOLUTION_ARCHITECTURE,
            "agent": {
                "role": "evolution_global_auditor",
                "session_id": agent_result.session_id,
                "episode_id": agent_result.episode_id,
                "model": client.model,
                "client_kind": agent_result.client_kind,
                "scripted": agent_result.scripted,
                "status": agent_result.status,
                "error_type": agent_error,
                "agent_turns": agent_result.agent_turns,
                "provider_http_attempts": agent_result.provider_http_attempts,
                "provider_retries": agent_result.provider_retries,
                "agent_turn_budget": total_budget,
                "phase_budget_policy": {
                    "global_audit_limit": audit_limit,
                    "synthesis_limit": synthesis_limit,
                    "same_agent_session_and_history": True,
                    "forced_synthesis_transition": forced_synthesis_transition,
                },
                "provider_attempt_budget_policy": provider_policy,
                "usage": dict(agent_result.usage),
                "trace": "evolution/agent_trace.jsonl",
            },
            "evaluation": dict(evaluation),
            "experience": dict(publication),
            "read_only_guards": {
                "source_report_sha256": projection["source"]["report_sha256"],
                "source_report_bytes": projection["source"]["report_bytes"],
                "projection_sha256": projection_sha,
                "framework_inputs_sha256": framework_sha,
                "framework_unchanged_during_agent": framework_unchanged,
                "agent_write_tools": [],
                "historical_source_run_written": False,
            },
            "terminal_status": terminal,
        }

    def validate_before_publish(
        evaluation: Mapping[str, Any],
        record: Mapping[str, Any],
    ) -> None:
        """Prove the complete accepted-report contract before mutating Experience."""

        record_hash = evaluation.get("approved_record_sha256")
        prospective_publication = {
            "published": True,
            "idempotent": False,
            "experience_id": record.get("experience_id"),
            "version": record.get("version"),
            "record_sha256": record_hash,
            # These placeholders exercise the exact schema and cross-field
            # semantics.  The publisher replaces them with measured hashes.
            "records_sha256": "0" * 64,
            "record_count": 1,
            "generation_view_sha256": "0" * 64,
            "manifest_sha256": "0" * 64,
        }
        _validate_evolution_report(
            build_report(evaluation, prospective_publication, "EVOLUTION_ACCEPTED"),
            report_schema,
        )

    evaluation, record, publication = evaluate_and_publish_evolution_audit(
        demo_root=root,
        run_root=source,
        audit=workspace.audit,
        initial_projection_sha256=projection_sha,
        evolution_run_id=run_id,
        allowed_repository_refs=workspace.repository_refs,
        framework_unchanged=framework_unchanged,
        schema_root=root / "schemas",
        before_publish=validate_before_publish,
    )
    atomic_write_json(evolution_dir / "trusted_evaluation.json", evaluation)
    if record is not None and evaluation["publishable"] is True:
        atomic_write_json(evolution_dir / "approved_experience_record.json", record)

    terminal = (
        "EVOLUTION_INCONCLUSIVE"
        if evaluation["verdict"] == "accepted" and publication.get("published") is not True
        else {
            "accepted": "EVOLUTION_ACCEPTED",
            "rejected": "EVOLUTION_REJECTED",
            "inconclusive": "EVOLUTION_INCONCLUSIVE",
        }[evaluation["verdict"]]
    )
    report = build_report(evaluation, publication, terminal)
    _validate_evolution_report(report, report_schema)
    atomic_write_json(output / "evolution_report.json", report)
    return output


__all__ = [
    "EvolutionRuntimeError",
    "build_global_evidence_projection",
    "load_aws_evolution_client",
    "run_evolution",
]
