#!/usr/bin/env python3
"""One cheap, non-streaming Holistic request with a diagnostic 300-second wait.

This measures transport only: no robot, tools, generated-code execution or retry.
It does not change the mainline route profile or its default request deadline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import run_fixed_family_diagnostic as runner


def request_body(model: str) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Follow the requested output format exactly."},
            {"role": "user", "content": (
                "This is a long-response transport test. Write exactly 1000 numbered records "
                "in order, one record per line, starting at 1. Each record must contain its "
                "number and a distinct 20-word sentence describing a fictional robot "
                "inspection observation. Produce the records directly as plain text. "
                "Do not provide code to generate them, an introduction, a summary, "
                "ellipsis or abbreviated ranges. Continue sequentially for as many "
                "records as the output limit allows."
            )},
        ],
        "max_tokens": 8192,
        "temperature": 0.0,
        "thinking": {"type": "disabled"},
        "stream": False,
    }


def result_fields(call: dict) -> dict:
    wait_s = call.get("response_wait_after_connection_s")
    crossed = (call.get("status") == "success"
               and isinstance(wait_s, (int, float)) and wait_s > 120)
    return {
        "successful_response_headers_after_120s": crossed,
        "interpretation": (
            "This request returned successfully more than 120 seconds after the "
            "connection was ready; a universal 120-second cutoff on this gateway "
            "is contradicted for this tested model/path. Opus behavior is not established."
            if crossed else
            "This request does not establish support beyond 120 seconds; a fast "
            "success or a failure cannot establish a universal gateway deadline."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("output must be fresh")
    output.mkdir(parents=True)
    config_path = runner.ROOT / "configs/diagnostics/fixed-family-v1-holistic-v32.json"
    config = runner.ExperimentConfig.from_path(config_path)
    client = runner.model_client(config, holistic=True)
    runner.write(output / "test_settings.json", {
        "formal": False, "model": client.config.model,
        "source_config": str(config_path), "client_timeout_s": 300,
        "mainline_profile_timeout_s": 120, "max_output_tokens": 8192,
        "physical_requests_maximum": 1, "stream": False, "thinking": "disabled",
        "robot_or_tool_execution": False, "transport": "curl HTTP/1.1",
        "connection_timeout_s": 15,
    })
    runner.write(output / "request.json", request_body(client.config.model))
    print("Holistic DeepSeek V3.2: one request, 8192 output-token limit, 300s client deadline", flush=True)
    # Keep the credential in curl's stdin, never argv, a tracked file or stdout.
    header = client.config.auth_header + ": " + client.config.auth_prefix + client.config.api_key
    curl_config = "header = " + json.dumps(header) + '\nheader = "Content-Type: application/json"\n'
    response_path = output / "response.json"
    completed = subprocess.run([
        "curl", "-q", "--config", "-", "--http1.1", "--silent", "--show-error",
        "--retry", "0", "--connect-timeout", "15", "--max-time", "300",
        "--request", "POST", "--url", client.config.endpoint_url,
        "--data-binary", "@" + str(output / "request.json"),
        "--output", str(response_path), "--write-out", "%{json}",
    ], input=curl_config, text=True, capture_output=True)
    timing = json.loads(completed.stdout)
    runner.write(output / "curl_timing.json", timing)
    http_status = int(timing["http_code"])
    payload = None
    if response_path.exists():
        try:
            payload = json.loads(response_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    status = "success"
    if completed.returncode != 0:
        status = "timeout" if completed.returncode == 28 else "transport_error"
    elif not 200 <= http_status < 300:
        status = "http_error"
    elif not isinstance(payload, dict) or not payload.get("choices"):
        status = "response_error"
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    start = float(timing["time_starttransfer"])
    ready = float(timing["time_pretransfer"])
    call = {
        "status": status, "http_status": http_status, "curl_exit_code": completed.returncode,
        "requested_model": client.config.model,
        "returned_model": payload.get("model") if isinstance(payload, dict) else None,
        "elapsed_s": timing["time_total"], "time_to_first_response_byte_s": start,
        "connection_ready_s": ready,
        "response_wait_after_connection_s": start - ready if start > 0 else None,
        "error": completed.stderr.replace(client.config.api_key, "<redacted>"),
        "usage": usage,
    }
    result = {**result_fields(call), "call": call}
    runner.write(output / "result.json", result)
    print(result["interpretation"], flush=True)
    print(f"status={status} elapsed_s={call['elapsed_s']:.3f}", flush=True)


if __name__ == "__main__":
    main()
