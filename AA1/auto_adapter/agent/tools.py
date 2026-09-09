# SPDX-License-Identifier: Apache-2.0
"""ToolSpec factories for the auto_adapter agent.

DESIGN.md §0.9 lists 8 MVP tools. This module exposes them as builder
functions so the orchestrator can compose phase-specific tool sets
(STUDY uses execute_python + inspect_skeleton; GENERATE adds
write_file + list_skeletons; VALIDATE adds ssh_dgx_exec + scp_*; etc.).

All 8 are now real. Network-dependent ones (execute_python /
ssh_dgx_exec / scp_*) take their session/host bindings as factory
args so the orchestrator owns session lifecycle.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from .react_loop import ToolSpec, _vlog


# ──────────────────────────────────────────────────────────────────────────
# File I/O (Mac local workspace = ./out/<robot_id>/)
# ──────────────────────────────────────────────────────────────────────────


def _resolve_under(root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` strictly under `root` (no .. traversal)."""
    p = (root / rel_path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in p.parents and p != root_resolved:
        raise PermissionError(
            f"path {rel_path!r} escapes workspace root {root_resolved}"
        )
    return p


def make_write_file_tool(workspace: Path) -> ToolSpec:
    """`write_file(path, content)` — writes a text file under workspace."""

    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    def _handler(inp: dict) -> dict:
        path = inp["path"]
        content = inp["content"]
        p = _resolve_under(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes_written": len(content.encode("utf-8"))}

    return ToolSpec(
        name="write_file",
        description=(
            "Write a text file under the workspace. Use this to save driver.py, "
            "skills.py, mcp_server.py, etc. `path` is workspace-relative; .. is rejected."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "workspace-relative file path"},
                "content": {"type": "string", "description": "full text content"},
            },
            "required": ["path", "content"],
        },
        handler=_handler,
    )


def make_read_file_tool(workspace: Path, extra_roots: list[Path] | None = None) -> ToolSpec:
    """`read_file(path)` — reads a text file from workspace OR any extra_roots.

    `extra_roots` is for read-only assets the agent needs to inspect (MJCFs,
    URDFs, framework skeleton source it may want to introspect, etc.).

    Security check is performed on the LEXICAL (unresolved) path so that a
    symlink the orchestrator placed in the workspace pointing OUT of the
    workspace (e.g. mjcf.xml → /…/sim/so101_mujoco.xml) reads fine. We do
    forbid `..` traversal in the input path itself, so the agent can't escape
    by writing `read_file("../../etc/passwd")`.
    """

    workspace = Path(workspace).resolve()
    allowed_roots = [workspace] + [Path(r).resolve() for r in (extra_roots or [])]

    def _handler(inp: dict) -> dict:
        path_in = inp["path"]
        p_candidate = Path(path_in)

        # Reject literal `..` segments in the input path to prevent escape.
        if any(part == ".." for part in p_candidate.parts):
            raise PermissionError(f"path {path_in!r} contains '..' (forbidden)")

        if p_candidate.is_absolute():
            # For absolute paths, check the absolute path (not its symlink
            # target) is under an allowed root. The agent should normally
            # only pass relative paths; absolute paths are for re-using a
            # path the agent saw in a prior read_file response.
            p = p_candidate
            if not any(root in p.parents or p == root for root in allowed_roots):
                raise PermissionError(
                    f"absolute path {path_in!r} not under any allowed root"
                )
        else:
            # Try each allowed root, checking the LEXICAL join (no resolve()
            # — symlinks may legitimately point outside the workspace).
            p = None
            for root in allowed_roots:
                cand = root / path_in  # no .resolve()
                if cand.exists():
                    p = cand
                    break
            if p is None:
                raise FileNotFoundError(
                    f"{path_in!r} not found under any allowed root: "
                    f"{[str(r) for r in allowed_roots]}"
                )
        text = p.read_text()
        return {"path": str(p), "content": text, "bytes": len(text.encode("utf-8"))}

    return ToolSpec(
        name="read_file",
        description=(
            "Read a UTF-8 text file. Use to inspect the MJCF, the URDF, "
            "framework skeleton source, or files you previously wrote. "
            "`path` may be workspace-relative or absolute under an allowed root."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_handler,
    )


# ──────────────────────────────────────────────────────────────────────────
# Skeleton introspection (so the agent knows what frameworks it can pick)
# ──────────────────────────────────────────────────────────────────────────


def _list_skeletons() -> list[dict]:
    """Reflect on auto_adapter.skeletons to surface available skeletons + their Spec."""
    from auto_adapter import skeletons as sk_pkg  # noqa: PLC0415

    out: list[dict] = []
    for name in getattr(sk_pkg, "__all__", []):
        obj = getattr(sk_pkg, name, None)
        if obj is None or not inspect.isclass(obj):
            continue
        # Skip the spec dataclasses themselves; surface only the skeleton classes
        if not name.endswith("Skeleton"):
            continue
        # Find the matching *Spec dataclass(es) in the same package
        spec_candidates = [
            n for n in getattr(sk_pkg, "__all__", []) if n.endswith("Spec")
        ]
        out.append(
            {
                "name": name,
                "doc": (obj.__doc__ or "").strip().split("\n")[0],
                "module": obj.__module__,
                "candidate_specs": spec_candidates,
            }
        )
    return out


def make_list_skeletons_tool() -> ToolSpec:
    """`list_skeletons()` — returns names + one-line docs of every skeleton."""

    def _handler(_inp: dict) -> dict:
        return {"skeletons": _list_skeletons()}

    return ToolSpec(
        name="list_skeletons",
        description=(
            "List all skeleton classes available in auto_adapter.skeletons. "
            "Use this when deciding which skeleton matches the robot class. "
            "Returns name, one-line doc, module, and candidate Spec dataclass names."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_handler,
    )


def _inspect_spec(spec_class: type) -> dict:
    """Reflect a dataclass into a JSON-friendly schema for LLM filling."""
    fields_out = []
    for f in dataclasses.fields(spec_class):
        # Default value handling
        if f.default is not dataclasses.MISSING:
            default = f.default
            has_default = True
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            try:
                default = f.default_factory()  # type: ignore[misc]
            except Exception:
                default = None
            has_default = True
        else:
            default = None
            has_default = False
        fields_out.append(
            {
                "name": f.name,
                "type": str(f.type),
                "has_default": has_default,
                "default": default if isinstance(default, (int, float, str, bool, list, dict, type(None))) else str(default),
                "required": not has_default,
            }
        )
    return {
        "spec_name": spec_class.__name__,
        "module": spec_class.__module__,
        "doc": (spec_class.__doc__ or "").strip(),
        "fields": fields_out,
    }


def make_inspect_skeleton_tool() -> ToolSpec:
    """`inspect_skeleton(skeleton_name)` — returns the Spec schema the agent must fill."""

    def _handler(inp: dict) -> dict:
        skel_name = inp["skeleton_name"]
        from auto_adapter import skeletons as sk_pkg  # noqa: PLC0415

        skel_cls = getattr(sk_pkg, skel_name, None)
        if skel_cls is None or not inspect.isclass(skel_cls):
            raise ValueError(f"skeleton {skel_name!r} not found in auto_adapter.skeletons")

        # Heuristic: every Skeleton subclass takes a `spec` of its matching Spec class.
        # Convention: ArmSerialDLSSkeleton ↔ ArmSpec, QuadrupedMPCGaitSkeleton ↔ QuadrupedSpec.
        spec_map = {
            "ArmSerialDLSSkeleton": "ArmSpec",
            "QuadrupedPDGaitSkeleton": "QuadrupedSpec",
            "HandFingertipDLSSkeleton": "HandFingertipDLSSpec",
            "StretchMobileManipulationSkeleton": "StretchMobileManipulationSpec",
            "BimanualSerialDLSSkeleton": "BimanualSerialDLSSpec",
        }
        spec_name = spec_map.get(skel_name)
        if spec_name is None:
            return {
                "skeleton": skel_name,
                "doc": (skel_cls.__doc__ or "").strip(),
                "spec_schema": None,
                "warning": "no Spec class registered for this skeleton in the spec_map",
            }
        spec_cls = getattr(sk_pkg, spec_name)
        return {
            "skeleton": skel_name,
            "doc": (skel_cls.__doc__ or "").strip(),
            "spec_schema": _inspect_spec(spec_cls),
        }

    return ToolSpec(
        name="inspect_skeleton",
        description=(
            "Given a skeleton class name (from list_skeletons), return the "
            "matching Spec dataclass schema you need to fill: "
            "field names, types, defaults, and which fields are required."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "skeleton_name": {"type": "string", "description": "e.g. ArmSerialDLSSkeleton"},
            },
            "required": ["skeleton_name"],
        },
        handler=_handler,
    )


# ──────────────────────────────────────────────────────────────────────────
# AgentCore CodeInterpreter: `execute_python`
# ──────────────────────────────────────────────────────────────────────────


def make_execute_python_tool(
    ci_client: Any,
    session_id: str,
    *,
    ci_id: str = "aws.codeinterpreter.v1",
    max_output_chars: int = 16_000,
) -> ToolSpec:
    """Bind execute_python to an EXISTING AgentCore CodeInterpreter session.

    The orchestrator owns the session lifecycle (start_code_interpreter_session
    on entry, stop_code_interpreter_session on exit) — this factory just hands
    the LLM a handle that streams code to that session and parses the result.

    Behavior matches study_agent_poc.py: returns {stdout, stderr, exit_code,
    execution_time_sec}; output streams are truncated at max_output_chars so
    a runaway print doesn't blow the context window.
    """

    def _handler(inp: dict) -> dict:
        if "code" not in inp:
            # Most common cause: the previous LLM turn hit max_tokens mid-tool_use,
            # so the input object never finished serializing. Tell the agent.
            raise ValueError(
                "execute_python: required `code` parameter is missing. Your "
                "previous turn likely truncated at max_tokens — break your code "
                "into smaller chunks (≤150 lines each) and call execute_python "
                "multiple times. The CI session preserves Python state across calls."
            )
        code = inp["code"]
        if not isinstance(code, str):
            raise TypeError(f"execute_python.code must be str, got {type(code).__name__}")

        _vlog(f"execute_python: invoke_code_interpreter START ({len(code)} chars)…")
        _t_ci = time.time()
        try:
            resp = ci_client.invoke_code_interpreter(
                codeInterpreterIdentifier=ci_id,
                sessionId=session_id,
                name="executeCode",
                arguments={"language": "python", "code": code},
            )
        except Exception as e:  # noqa: BLE001 — surface CI timeout/transport
            _vlog(f"execute_python: invoke_code_interpreter FAILED after "
                  f"{time.time()-_t_ci:.0f}s: {type(e).__name__}: {e}")
            return {"stdout": "", "stderr": f"CodeInterpreter error: "
                    f"{type(e).__name__}: {e}", "exit_code": -1,
                    "execution_time_sec": time.time() - _t_ci}
        _vlog(f"execute_python: invoke returned in {time.time()-_t_ci:.0f}s, "
              f"draining stream…")

        stdout = stderr = ""
        exit_code: Optional[int] = None
        exec_time: float = 0.0
        for ev in resp["stream"]:
            if "result" in ev:
                sc = ev["result"].get("structuredContent", {}) or {}
                stdout = sc.get("stdout", "") or stdout
                stderr = sc.get("stderr", "") or stderr
                exit_code = sc.get("exitCode", exit_code)
                exec_time = float(sc.get("executionTime", exec_time) or 0.0)
        _vlog(f"execute_python: stream drained in {time.time()-_t_ci:.0f}s "
              f"(exit={exit_code}, exec_time={exec_time:.1f}s)")

        def _truncate(s: str) -> str:
            return s if len(s) <= max_output_chars else (
                s[:max_output_chars] + f"\n…(+{len(s) - max_output_chars} chars truncated)"
            )

        return {
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "exit_code": exit_code,
            "execution_time_sec": exec_time,
        }

    return ToolSpec(
        name="execute_python",
        description=(
            "Run Python code in a sandboxed AgentCore CodeInterpreter session. "
            "Returns {stdout, stderr, exit_code, execution_time_sec}. Use to "
            "parse MJCF/URDF, derive joint indices, or any pre-DGX numerical work. "
            "State persists across calls within the same session (you can import "
            "once, then reuse the module in later calls)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute"},
            },
            "required": ["code"],
        },
        handler=_handler,
    )


# ──────────────────────────────────────────────────────────────────────────
# DGX bridge: `ssh_dgx_exec`, `scp_to_dgx`, `scp_from_dgx`
# (subprocess wrappers over the user's existing Tailscale ssh config)
# ──────────────────────────────────────────────────────────────────────────


_SSH_STDOUT_CAP = 16_000
_SSH_STDERR_CAP = 4_000


def _run_subprocess(argv: list[str], timeout_sec: float) -> dict:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        return {
            "argv": argv,
            "exit_code": -1,
            "stdout": (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""))[:_SSH_STDOUT_CAP],
            "stderr": (e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))[:_SSH_STDERR_CAP],
            "error": f"timeout after {timeout_sec}s",
        }
    return {
        "argv": argv,
        "exit_code": cp.returncode,
        "stdout": cp.stdout[:_SSH_STDOUT_CAP],
        "stderr": cp.stderr[:_SSH_STDERR_CAP],
    }


def make_ssh_dgx_exec_tool(
    dgx_host: str = "YOUR_DGX_HOST",
    *,
    remote_workspace: Optional[str] = None,
    default_timeout_sec: float = 60.0,
    extra_ssh_opts: Optional[list[str]] = None,
) -> ToolSpec:
    """Run a command on the DGX over the user's existing ssh config.

    If `remote_workspace` is set, every command is prefixed with `cd <ws> && `
    so the agent's relative paths line up with scp_to_dgx targets.
    """

    ssh_base = ["ssh", "-o", "BatchMode=yes"]
    if extra_ssh_opts:
        ssh_base += list(extra_ssh_opts)

    def _handler(inp: dict) -> dict:
        cmd = inp["command"]
        if not isinstance(cmd, str):
            raise TypeError(f"command must be str, got {type(cmd).__name__}")
        timeout = float(inp.get("timeout_sec", default_timeout_sec))
        remote_cmd = cmd
        if remote_workspace:
            remote_cmd = f"cd {shlex.quote(remote_workspace)} && ({cmd})"
        return _run_subprocess(ssh_base + [dgx_host, remote_cmd], timeout)

    desc = (
        f"Run a shell command on the DGX ({dgx_host}) via ssh. "
        "Returns {exit_code, stdout, stderr}. Use to launch real MuJoCo runs, "
        "record videos, or run Phase-3 behavior tests on GPU."
    )
    if remote_workspace:
        desc += f" Commands run inside `{remote_workspace}`."

    return ToolSpec(
        name="ssh_dgx_exec",
        description=desc,
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {"type": "number", "default": default_timeout_sec},
            },
            "required": ["command"],
        },
        handler=_handler,
    )


# ──────────────────────────────────────────────────────────────────────────
# Local-mode runtime: `local_exec` (replaces ssh_dgx_exec when running on Mac)
# ──────────────────────────────────────────────────────────────────────────


def make_local_exec_tool(
    workspace: Path,
    *,
    default_timeout_sec: float = 120.0,
    env_overlay: Optional[dict[str, str]] = None,
    python_path_prepend: Optional[list[Path]] = None,
    python_executable: Optional[Path] = None,
) -> ToolSpec:
    """Run a shell command locally with cwd=workspace.

    Local-mode counterpart to `ssh_dgx_exec`. The runtime env is wired so the
    agent's `python validate.py` runs in the SAME venv as the orchestrator
    (Mujoco, anthropic, boto3 all importable). Specifically:
      * PYTHONPATH gets prepended with the workspace + any extra roots.
      * PATH gets prepended with the python_executable's bin dir, so `python`
        resolves to that interpreter. Defaults to `sys.executable` (the
        orchestrator's own interpreter — virtually always what you want).
    """
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    import os as _os  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415

    # Use sys.prefix (the venv root) rather than sys.executable — on uv-managed
    # venvs, sys.executable is a symlink and .resolve() follows it to the
    # shared interpreter (which does NOT have the project's deps installed).
    if python_executable is not None:
        py_exe = Path(python_executable)
        py_bin_dir = py_exe.parent
    else:
        py_bin_dir = Path(_sys.prefix) / "bin"
        py_exe = py_bin_dir / "python"

    base_env = dict(_os.environ)
    if env_overlay:
        base_env.update(env_overlay)
    if python_path_prepend:
        prepend = ":".join(str(Path(p).resolve()) for p in python_path_prepend)
        existing = base_env.get("PYTHONPATH", "")
        base_env["PYTHONPATH"] = f"{prepend}:{existing}" if existing else prepend
    # Make sure the workspace itself is importable too (for `import driver`)
    ws_str = str(workspace)
    if ws_str not in base_env.get("PYTHONPATH", ""):
        base_env["PYTHONPATH"] = f"{ws_str}:{base_env.get('PYTHONPATH', '')}"
    # Force `python` to resolve to the orchestrator's venv interpreter
    base_env["PATH"] = f"{py_bin_dir}:{base_env.get('PATH', '')}"
    base_env["VIRTUAL_ENV"] = str(py_bin_dir.parent)

    def _handler(inp: dict) -> dict:
        cmd = inp["command"]
        if not isinstance(cmd, str):
            raise TypeError(f"command must be str, got {type(cmd).__name__}")
        timeout = float(inp.get("timeout_sec", default_timeout_sec))
        try:
            cp = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(workspace),
                env=base_env,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "exit_code": -1,
                "stdout": (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""))[:_SSH_STDOUT_CAP],
                "stderr": (e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))[:_SSH_STDERR_CAP],
                "error": f"timeout after {timeout}s",
            }
        return {
            "exit_code": cp.returncode,
            "stdout": cp.stdout[:_SSH_STDOUT_CAP],
            "stderr": cp.stderr[:_SSH_STDERR_CAP],
        }

    return ToolSpec(
        name="local_exec",
        description=(
            f"Run a shell command locally in the workspace ({workspace}). "
            "Returns {exit_code, stdout, stderr}. Use to run `python validate.py`, "
            "`python demo.py`, etc. The workspace and the auto_adapter repo are "
            "on PYTHONPATH, so scripts can `import auto_adapter.skeletons` and "
            "`import driver` without managing sys.path. The `python` command "
            "is wired to the orchestrator's venv — mujoco, numpy, etc. are "
            "already installed; DO NOT run `pip install`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {"type": "number", "default": default_timeout_sec},
            },
            "required": ["command"],
        },
        handler=_handler,
    )


def make_scp_tools(
    dgx_host: str = "YOUR_DGX_HOST",
    *,
    local_workspace: Path,
    remote_workspace: str,
    default_timeout_sec: float = 60.0,
    extra_scp_opts: Optional[list[str]] = None,
) -> tuple[ToolSpec, ToolSpec]:
    """Build (scp_to_dgx, scp_from_dgx). All paths are workspace-relative."""
    local_workspace = Path(local_workspace).resolve()
    local_workspace.mkdir(parents=True, exist_ok=True)
    scp_base = ["scp", "-o", "BatchMode=yes"]
    if extra_scp_opts:
        scp_base += list(extra_scp_opts)

    def _quote_remote(rel: str) -> str:
        return f"{dgx_host}:{shlex.quote(remote_workspace + '/' + rel)}"

    def _to(inp: dict) -> dict:
        local_rel = inp["local_relpath"]
        remote_rel = inp["remote_relpath"]
        local_path = _resolve_under(local_workspace, local_rel)
        if not local_path.exists():
            raise FileNotFoundError(f"local file not found: {local_path}")
        timeout = float(inp.get("timeout_sec", default_timeout_sec))
        argv = scp_base + [str(local_path), _quote_remote(remote_rel)]
        return _run_subprocess(argv, timeout) | {
            "local_path": str(local_path),
            "remote_path": f"{dgx_host}:{remote_workspace}/{remote_rel}",
        }

    def _from(inp: dict) -> dict:
        remote_rel = inp["remote_relpath"]
        local_rel = inp["local_relpath"]
        local_path = _resolve_under(local_workspace, local_rel)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = float(inp.get("timeout_sec", default_timeout_sec))
        argv = scp_base + [_quote_remote(remote_rel), str(local_path)]
        return _run_subprocess(argv, timeout) | {
            "local_path": str(local_path),
            "remote_path": f"{dgx_host}:{remote_workspace}/{remote_rel}",
        }

    to_spec = ToolSpec(
        name="scp_to_dgx",
        description=(
            f"Copy a local workspace file ({local_workspace}) to the DGX "
            f"workspace ({dgx_host}:{remote_workspace}). Both paths are workspace-relative."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "local_relpath": {"type": "string"},
                "remote_relpath": {"type": "string"},
                "timeout_sec": {"type": "number", "default": default_timeout_sec},
            },
            "required": ["local_relpath", "remote_relpath"],
        },
        handler=_to,
    )
    from_spec = ToolSpec(
        name="scp_from_dgx",
        description=(
            f"Copy a DGX workspace file ({dgx_host}:{remote_workspace}) back to "
            f"the local workspace ({local_workspace}). Both paths are workspace-relative."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "remote_relpath": {"type": "string"},
                "local_relpath": {"type": "string"},
                "timeout_sec": {"type": "number", "default": default_timeout_sec},
            },
            "required": ["remote_relpath", "local_relpath"],
        },
        handler=_from,
    )
    return to_spec, from_spec


# ──────────────────────────────────────────────────────────────────────────
# Convenience: bundle the MVP-ready tools for quick orchestrator use
# ──────────────────────────────────────────────────────────────────────────


def make_mvp_local_tools(
    workspace: Path,
    read_extra_roots: list[Path] | None = None,
) -> list[ToolSpec]:
    """Returns the 4 fully-functional, local-only MVP tools.

    Phase 1 STUDY can already use list_skeletons + inspect_skeleton + read_file.
    Phase 2 GENERATE can use write_file to drop driver.py / skills.py into
    the workspace. Network-dependent tools (execute_python / ssh_dgx_exec
    / scp_*) are added by the orchestrator on top of these.
    """
    return [
        make_write_file_tool(workspace),
        make_read_file_tool(workspace, extra_roots=read_extra_roots),
        make_list_skeletons_tool(),
        make_inspect_skeleton_tool(),
    ]
