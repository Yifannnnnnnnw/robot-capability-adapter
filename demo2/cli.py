"""CLI for the Demo2 AutoAdapter 1.0-compatible MuJoCo line."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pipeline import (
    SUPPORTED_ROBOTS,
    Demo2Error,
    ModelSettings,
    inspect_package,
    run_all,
    run_robot,
)


def _load_local_env() -> None:
    """Load ignored local Demo2 credentials without overriding shell values."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("AUTOADAPTER_"):
            os.environ.setdefault(key, value.strip().strip("'\""))


def _model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("bedrock", "openai"),
        default=os.environ.get("AUTOADAPTER_MODEL_PROVIDER", "bedrock"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AUTOADAPTER_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--base-url", default=os.environ.get("AUTOADAPTER_MODEL_API_BASE_URL"))
    parser.add_argument("--endpoint-path", default="/v1/chat/completions")
    parser.add_argument("--api-key-env", default="AUTOADAPTER_MODEL_API_KEY")
    parser.add_argument(
        "--auth-header",
        default=os.environ.get("AUTOADAPTER_MODEL_API_AUTH_HEADER", "Authorization"),
    )
    parser.add_argument(
        "--auth-prefix",
        default=os.environ.get("AUTOADAPTER_MODEL_API_AUTH_PREFIX", "Bearer "),
    )
    parser.add_argument(
        "--thinking",
        choices=("enabled", "disabled"),
        default=os.environ.get("AUTOADAPTER_MODEL_THINKING") or None,
    )
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--no-video", action="store_true")


def _settings(args: argparse.Namespace) -> ModelSettings:
    return ModelSettings(
        provider=args.provider,
        model=args.model,
        region=args.region,
        base_url=args.base_url,
        endpoint_path=args.endpoint_path,
        api_key_env=args.api_key_env,
        auth_header=args.auth_header,
        auth_prefix=args.auth_prefix,
        thinking=args.thinking,
        max_tokens=args.max_tokens,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m demo2.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--robot", required=True, choices=SUPPORTED_ROBOTS)

    smoke = commands.add_parser("smoke", help="formal dynamic Stage1/Blue/1.0 generation run")
    smoke.add_argument("--robot", required=True, choices=SUPPORTED_ROBOTS)
    smoke.add_argument("--output", type=Path, required=True, help="run root; robot subdirectory is created")
    _model_arguments(smoke)

    all_command = commands.add_parser("run-all", help="formal dynamic run for all admitted robots")
    all_command.add_argument("--output", type=Path, required=True)
    _model_arguments(all_command)

    calibrate = commands.add_parser(
        "calibrate-all",
        help="physical calibration for all admitted robots using checked 1.0-derived reference drivers",
    )
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--no-video", action="store_true")
    return parser


def main() -> int:
    _load_local_env()
    args = _parser().parse_args()
    try:
        if args.command == "inspect":
            result = inspect_package(args.robot)
        elif args.command == "smoke":
            result = run_robot(
                args.robot,
                args.output,
                settings=_settings(args),
                record_video=not args.no_video,
            )
        elif args.command == "run-all":
            result = run_all(
                args.output,
                settings=_settings(args),
                record_video=not args.no_video,
            )
        else:
            result = run_all(
                args.output,
                reference_calibration=True,
                record_video=not args.no_video,
            )
    except Demo2Error as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
