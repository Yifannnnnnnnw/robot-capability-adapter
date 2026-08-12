#!/usr/bin/env python3
"""Propose one experimental post-closure Experience Candidate."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT / "src"))

from autoadapter2.evolution import propose_experience_candidate  # noqa: E402
from autoadapter2.foundation.errors import ContractError  # noqa: E402
from autoadapter2.generation.model_api import ModelApiClient, ModelApiConfig  # noqa: E402
from autoadapter2.integration.artifacts import load_json_artifact  # noqa: E402

EVOLUTION_PROMPT = """
Return exactly one JSON object and no Markdown. It must contain exactly these keys and
JSON value types:
- recipient_class: a JSON string enum with exactly one of "design" or "implementation".
- lesson: a nonempty JSON string.
- applicability: a JSON object with exactly these keys and types:
  - robot_model_id: a JSON string.
  - robot_configuration_id: a JSON string.
  - sdk_entry_id: a JSON string or JSON null.
  - granularity_condition: a JSON string.
  - capability_effect_scope: a nonempty JSON array of nonempty JSON strings.
  - observation_condition: a JSON string.
- limitations: a JSON array of nonempty JSON strings.
- invalidation_conditions: a nonempty JSON array of nonempty JSON strings.
Use only a bounded public lesson supported by the supplied sanitized evidence digest.
Copy robot_model_id, robot_configuration_id, sdk_entry_id, and granularity_condition
exactly from the supplied sanitized evidence digest. Do not paraphrase, normalize, infer,
or invent these applicability identity values. Keep capability_effect_scope and
observation_condition model-authored from the supplied public evidence.
All free-text fields must be plain prose only. Do not use code fences, assignments,
function signatures or call expressions, braces, semicolons, arrows, filenames, or
source snippets. Refer to API concepts in ordinary prose rather than reproducing syntax.
Free-text fields must avoid these reserved privacy/evidence words and their plural forms:
threshold, criterion, case, seed, video, frame, camera, trace, diagnostic, source, code,
MuJoCo, qpos, qvel, truth, private, translation, transport, credential, password, secret,
token, prompt, LLM, oracle, expected, winning, raw.
Do not include source code, private evaluation details, diagnostics payloads, traces,
video or camera details, simulator truth, credentials, prompts, or model output.
For design recipients sdk_entry_id must be null; for implementation recipients it must
identify the applicable SDK Entry. Return JSON values only.
""".strip()


def model_api_agent(client: Any) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Adapt the existing JSON model client to the EvolutionAgent protocol."""

    def call(evidence_digest: Mapping[str, Any]) -> Mapping[str, Any]:
        return client.generate_json(
            stage="evolution",
            prompt=EVOLUTION_PROMPT,
            inputs=evidence_digest,
        )

    return call


def main(
    argv: list[str] | None = None,
    *,
    model_api_factory: Callable[[], Any] | None = None,
) -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Compile an experimental, human-review-required Experience Candidate "
            "from a verified closed first-G2 run."
        )
    )
    parser.add_argument("--root", type=Path, default=project_root)
    parser.add_argument("--closure", "--run-closure", dest="closure", type=Path, required=True)
    parser.add_argument(
        "--closure-seal",
        "--run-closure-seal",
        dest="closure_seal",
        type=Path,
        required=True,
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--evolution-cases", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--candidate-json",
        type=Path,
        help="JSON file containing only the callback semantic field set for a manual smoke.",
    )
    source.add_argument(
        "--model-api",
        action="store_true",
        help="Use ModelApiClient.from_environment through the sanitized EvolutionAgent input.",
    )
    args = parser.parse_args(argv)

    try:
        candidate_fields = None
        evolution_agent = None
        if args.candidate_json is not None:
            candidate_fields = load_json_artifact(args.candidate_json).value
        else:
            factory = model_api_factory or (
                lambda: ModelApiClient(ModelApiConfig.from_environment())
            )
            evolution_agent = model_api_agent(factory())
        result = propose_experience_candidate(
            args.root,
            args.closure,
            args.closure_seal,
            args.case_id,
            evolution_cases_root=args.evolution_cases,
            candidate_fields=candidate_fields,
            evolution_agent=evolution_agent,
        )
    except (ContractError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"experience candidate rejected: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "candidate": str(result.candidate_path),
                "candidate_hash": result.candidate_hash,
                "candidate_seal": str(result.candidate_seal_path),
                "declassification_report": str(result.declassification_report_path),
                "declassification_report_hash": result.declassification_report_hash,
                "declassification_report_seal": str(result.declassification_report_seal_path),
                "evidence_digest_hash": result.evidence_digest_hash,
                "closure_hash": result.closure_hash,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
