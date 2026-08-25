"""Bounded local Python/MuJoCo development probes for Driver Synthesis.

Probe processes run against a public-only package copy in the condition
workspace.  The admitted robot package root is intentionally never placed in
the child environment or passed as a script argument: it may contain private
validation inputs and calibration references.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import select
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

from autoadapter2.libraries import RobotPackage


ProbeCondition = Literal["skeleton-assisted", "from-scratch"]


class ProbeError(RuntimeError):
    """Raised when a development probe request is invalid or cannot be staged."""


class ProbeSourceError(ProbeError):
    """Raised when probe or candidate source crosses the public code boundary."""


@dataclass(frozen=True)
class ProbeBudget:
    """Hard limits shared by one condition's local probe batch."""

    # ``None`` is the file-workflow mainline: ReAct has a turn budget but no
    # second aggregate tool-call ceiling.  Focused legacy/batch callers may
    # still supply a finite count when that count is itself the subject of a
    # diagnostic test.
    max_requests: int | None = None
    max_complete_driver_checks: int = 1
    timeout_s: float = 30.0
    max_output_chars: int = 24000
    max_steps: int = 4000
    max_sim_time_s: float = 20.0

    def __post_init__(self) -> None:
        if self.max_requests is not None and self.max_requests <= 0:
            raise ValueError("max_requests must be positive or None")
        if (
            isinstance(self.max_complete_driver_checks, bool)
            or not isinstance(self.max_complete_driver_checks, int)
            or self.max_complete_driver_checks not in {1, 2}
        ):
            raise ValueError("max_complete_driver_checks must be 1 or 2")
        if self.timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.max_sim_time_s <= 0.0:
            raise ValueError("max_sim_time_s must be positive")


@dataclass(frozen=True)
class ProbeRequest:
    probe_id: str
    script: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int = 0) -> ProbeRequest:
        probe_id = value.get("probe_id")
        script = value.get("script")
        if not isinstance(probe_id, str) or not probe_id.strip():
            raise ProbeError(f"probe_requests[{index}].probe_id must be non-empty")
        if not isinstance(script, str) or not script.strip():
            raise ProbeError(f"probe_requests[{index}].script must be non-empty")
        return cls(probe_id=probe_id.strip(), script=script)


@dataclass(frozen=True)
class PublicProbeWorkspace:
    root: Path
    scene_path: Path
    skeleton_root: Path | None
    python_root: Path | None


_PERSISTENT_RESULT_PREFIX = "__AUTOADAPTER_PERSISTENT_RESULT__="


def _sandbox_literal(path: Path) -> str:
    """Return one quoted Seatbelt literal without shell interpolation."""

    return json.dumps(os.fspath(path.resolve()))


@functools.lru_cache(maxsize=1)
def _seatbelt_available() -> bool:
    """Return whether a nested macOS Seatbelt profile can be applied here."""

    executable = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not executable.is_file():
        return False
    try:
        result = subprocess.run(
            [
                os.fspath(executable),
                "-p",
                "(version 1) (allow default)",
                "/usr/bin/true",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _sandboxed_python_command(
    *,
    public_workspace: PublicProbeWorkspace,
    workspace: Path,
    arguments: Sequence[str],
) -> list[str]:
    """Build one interpreter command with mandatory OS-level confinement.

    The bootstrap always installs a Python audit hook and the process must also
    enter macOS Seatbelt.  We fail closed when that OS boundary is unavailable:
    MuJoCo is a native extension and its file APIs do not emit every CPython
    audit event, so an audit-hook-only process is not an isolation boundary.
    """

    interpreter = Path(sys.executable).resolve()
    python_prefixes = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    command = [sys.executable, *arguments]
    if not _seatbelt_available():
        raise ProbeError(
            "OS-level Python/MuJoCo probe sandbox is unavailable; refusing model code execution"
        )

    readable_roots = {
        *python_prefixes,
        workspace.resolve(),
        public_workspace.root.resolve(),
    }
    if public_workspace.python_root is not None:
        readable_roots.add(public_workspace.python_root.resolve())
    read_denials = {
        Path("/Users"),
        Path("/Volumes"),
        Path("/etc"),
        Path("/home"),
        Path("/root"),
        Path("/Library/Keychains"),
        Path("/private/etc"),
        Path("/private/tmp"),
        Path("/private/var/folders"),
        Path("/private/var/db"),
        Path("/private/var/tmp"),
    }
    profile_parts = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny process-fork)",
        "(deny process-exec)",
        f"(allow process-exec (literal {_sandbox_literal(interpreter)}))",
        "(deny file-read* "
        + " ".join(f"(subpath {_sandbox_literal(path)})" for path in sorted(read_denials))
        + ")",
        "(allow file-read* "
        + " ".join(f"(subpath {_sandbox_literal(path)})" for path in sorted(readable_roots))
        + ")",
        "(deny file-write*)",
        f"(allow file-write* (subpath {_sandbox_literal(workspace)}))",
    ]
    return ["/usr/bin/sandbox-exec", "-p", " ".join(profile_parts), *command]


_RUNTIME_GUARD_BOOTSTRAP = r'''
import contextlib
import io
import json
import os
import platform
import subprocess
import sys
import traceback

# MuJoCo's macOS package shells out to ``sysctl`` once during import solely to
# detect Rosetta.  The parent already launches its current native interpreter;
# keep the probe sandbox process-free by supplying that one harmless result
# in-process, then restore subprocess.run before any model code executes.
_original_subprocess_run = subprocess.run
if platform.system() == "Darwin":
    class _NativeProcessResult:
        stdout = b"0\n"

    def _native_sysctl_result(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if command == ["sysctl", "-n", "sysctl.proc_translated"]:
            return _NativeProcessResult()
        raise RuntimeError("process creation is unavailable in execute_python")

    subprocess.run = _native_sysctl_result

import mujoco

subprocess.run = _original_subprocess_run


def _blocked_plugin_loader(*_args, **_kwargs):
    raise PermissionError("MuJoCo plugin loading is unavailable in execute_python")


# These pybind entry points can call dlopen without a CPython ``ctypes.*``
# audit event.  Disable them before any model-authored source can run.
for _plugin_loader_name in ("mj_loadPluginLibrary", "mj_loadAllPluginLibraries"):
    if hasattr(mujoco, _plugin_loader_name):
        setattr(mujoco, _plugin_loader_name, _blocked_plugin_loader)


# This non-removable Python audit hook complements mandatory Seatbelt.  It
# covers dynamic getattr/import/open paths that the parent AST audit cannot
# reliably recognise; it is defense-in-depth, not a substitute for the OS
# boundary because native MuJoCo file APIs do not emit every CPython event.
_phase_workspace = os.path.realpath(os.environ["AUTOADAPTER_PROBE_WORKSPACE"])
_read_roots = {
    _phase_workspace,
    os.path.realpath(os.environ["AUTOADAPTER_PROBE_PUBLIC_PACKAGE"]),
    os.path.realpath(sys.prefix),
    os.path.realpath(sys.base_prefix),
}
_interpreter_roots = tuple(sorted({
    os.path.realpath(sys.prefix),
    os.path.realpath(sys.base_prefix),
}))
_python_root_for_audit = os.environ.get("AUTOADAPTER_PROBE_PYTHON_ROOT")
if _python_root_for_audit:
    _read_roots.add(os.path.realpath(_python_root_for_audit))
_read_roots = tuple(sorted(_read_roots))
_write_roots = (_phase_workspace,)
_special_files = {"/dev/null", "/dev/urandom", "/dev/random"}
_blocked_import_roots = {
    "_ctypes", "_posixsubprocess", "_socket", "asyncio", "cffi", "ctypes",
    "ftplib", "http", "httpx", "importlib", "multiprocessing", "pkgutil",
    "requests", "runpy", "shutil", "socket", "ssl", "subprocess", "sys",
    "telnetlib", "urllib",
}
_compile_authorised = False
_trusted_dynamic_compile_modules = {"ast", "dataclasses"}


def _normalise_audit_path(value):
    if isinstance(value, int):
        raise PermissionError("execute_python cannot open inherited file descriptors")
    if value is None:
        value = os.getcwd()
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        raise PermissionError("execute_python filesystem path must be text")
    return os.path.realpath(os.path.abspath(value))


def _under_any(path, roots):
    if path in _special_files:
        return True
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def _require_path(value, roots, operation):
    path = _normalise_audit_path(value)
    if not _under_any(path, roots):
        raise PermissionError(
            "execute_python %s is outside the isolated phase workspace" % operation
        )


def _runtime_audit(event, args):
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        writing = (
            isinstance(mode, str) and any(marker in mode for marker in "wax+")
        ) or (
            isinstance(flags, int)
            and bool(
                flags
                & (
                    os.O_WRONLY
                    | os.O_RDWR
                    | os.O_CREAT
                    | os.O_TRUNC
                    | os.O_APPEND
                )
            )
        )
        _require_path(path, _write_roots if writing else _read_roots, "file access")
        return
    if event in {"os.listdir", "os.scandir", "os.chdir"} and args:
        _require_path(args[0], _read_roots, event)
        return
    if event in {
        "os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
        "os.truncate", "os.utime",
    } and args:
        _require_path(args[0], _write_roots, event)
        return
    if event in {"os.rename", "os.replace", "os.link", "os.symlink"}:
        if args:
            _require_path(args[0], _write_roots, event)
        if len(args) > 1:
            _require_path(args[1], _write_roots, event)
        return
    if (
        event
        in {
            "os.system",
            "os.fork",
            "os.forkpty",
            "os.kill",
            "os.killpg",
            "subprocess.Popen",
        }
        or event.startswith("os.exec")
        or event.startswith("os.spawn")
        or event.startswith("os.posix_spawn")
    ):
        raise PermissionError("process creation is unavailable in execute_python")
    if event.startswith("socket."):
        raise PermissionError("network access is unavailable in execute_python")
    if event.startswith("ctypes."):
        raise PermissionError("native dynamic loading is unavailable in execute_python")
    if event == "import" and args:
        module_root = str(args[0]).split(".", 1)[0]
        if module_root in _blocked_import_roots:
            raise PermissionError(
                "import %s is unavailable in execute_python" % module_root
            )
    if event == "compile":
        filename = args[1] if len(args) > 1 else ""
        if _compile_authorised:
            return
        if isinstance(filename, str) and not filename.startswith("<"):
            _require_path(filename, _read_roots, "compile")
            return
        # ``dataclasses`` creates generated methods with ``compile(...,
        # '<string>', ...)`` and ``ast.parse`` compiles to an AST with an
        # angle-bracket filename.  Both are required by the staged trusted
        # skeletons and ordinary probe diagnostics.  Admit only calls whose
        # executing frame is the immutable matching stdlib module; a
        # model-authored frame cannot opt in by changing ``__name__``.
        caller = sys._getframe(1)
        caller_module = str(caller.f_globals.get("__name__", ""))
        caller_path = os.path.realpath(caller.f_code.co_filename)
        if (
            caller_module in _trusted_dynamic_compile_modules
            and os.path.basename(caller_path) == caller_module + ".py"
            and _under_any(caller_path, _interpreter_roots)
        ):
            return
        raise PermissionError("dynamic compilation is unavailable in execute_python")
    if event in {"builtins.input", "builtins.input/result"}:
        raise PermissionError("stdin access is unavailable in execute_python")


sys.addaudithook(_runtime_audit)
'''


_PERSISTENT_BOOTSTRAP = _RUNTIME_GUARD_BOOTSTRAP + r'''


_public_root = os.environ.get("AUTOADAPTER_PROBE_PUBLIC_PACKAGE")
if _public_root:
    # ``-I`` removes the working directory from the import path.  Keep the
    # staged public package importable even when the session started before a
    # model wrote the candidate driver.py into it.
    sys.path.insert(0, _public_root)
_python_root = os.environ.get("AUTOADAPTER_PROBE_PYTHON_ROOT")
if _python_root:
    sys.path.insert(0, _python_root)


_max_steps = int(os.environ["AUTOADAPTER_PROBE_MAX_STEPS"])
_max_sim_time_s = float(os.environ["AUTOADAPTER_PROBE_MAX_SIM_TIME_S"])
_total_steps = 0
_original_mj_step = mujoco.mj_step
_state = {"__name__": "__execute_python__", "__builtins__": __builtins__}


def _requested_steps(args, kwargs):
    value = kwargs.get("nstep", args[0] if args else 1)
    try:
        steps = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("execute_python nstep must be an integer") from exc
    if steps < 0:
        raise RuntimeError("execute_python nstep must not be negative")
    return steps


def _bounded_mj_step(model, data, *args, **kwargs):
    global _total_steps
    steps = _requested_steps(args, kwargs)
    if _total_steps + steps > _max_steps:
        raise RuntimeError("execute_python control-step budget exceeded")
    result = _original_mj_step(model, data, *args, **kwargs)
    _total_steps += steps
    if float(data.time) > _max_sim_time_s:
        raise RuntimeError("execute_python simulated-time budget exceeded")
    return result


mujoco.mj_step = _bounded_mj_step


for _line in sys.stdin:
    try:
        _request = json.loads(_line)
        _code = _request["code"]
        if _request.get("invalidate_driver"):
            sys.modules.pop("driver", None)
            _state.pop("driver", None)
        _stdout = io.StringIO()
        _stderr = io.StringIO()
        _before = _total_steps
        _ok = True
        _error = None
        with contextlib.redirect_stdout(_stdout), contextlib.redirect_stderr(_stderr):
            try:
                _compile_authorised = True
                try:
                    _compiled = compile(_code, "<execute_python>", "exec")
                finally:
                    _compile_authorised = False
                exec(_compiled, _state, _state)
            except BaseException as _exc:
                _ok = False
                _error = {"type": type(_exc).__name__, "message": str(_exc)}
                traceback.print_exc()
        _response = {
            "ok": _ok,
            "stdout": _stdout.getvalue(),
            "stderr": _stderr.getvalue(),
            "physics_steps": _total_steps - _before,
            "physics_steps_total": _total_steps,
            "error": _error,
        }
    except BaseException as _exc:
        _response = {
            "ok": False,
            "stdout": "",
            "stderr": "",
            "physics_steps": 0,
            "physics_steps_total": _total_steps,
            "error": {"type": type(_exc).__name__, "message": str(_exc)},
        }
    sys.__stdout__.write("__AUTOADAPTER_PERSISTENT_RESULT__=" + json.dumps(_response) + "\n")
    sys.__stdout__.flush()
'''


class PersistentPythonSession:
    """One credential-free, public-only Python/MuJoCo process per phase.

    The process speaks a tiny JSON-lines protocol.  Each ``execute`` call is
    evaluated in the same globals dictionary, so model-authored exploratory
    state survives across successful calls. A timeout or worker loss discards
    that state; the next explicit call gets a clean worker while the parent
    preserves call accounting and conservatively exhausts unknown step use.
    """

    def __init__(
        self,
        *,
        public_workspace: PublicProbeWorkspace,
        workspace: str | Path,
        condition: ProbeCondition | str,
        budget: ProbeBudget,
    ) -> None:
        self.public_workspace = public_workspace
        self.workspace = Path(workspace).resolve()
        self.condition = str(condition)
        self.budget = budget
        self._calls = 0
        self._closed = False
        self._process: subprocess.Popen[bytes] | None = None
        self._steps_used = 0
        self._worker_step_offset = 0
        self._restart_pending = False
        self._start_process()

    def _start_process(self) -> bool:
        """Start one confined worker and report whether it replaces a lost one."""

        if self._closed:
            raise ProbeError("persistent execute_python session is closed")
        restarted = self._restart_pending
        self._restart_pending = False
        self._worker_step_offset = self._steps_used
        env = _probe_environment(
            public_workspace=self.public_workspace,
            workspace=self.workspace,
            budget=self.budget,
            max_steps=max(0, self.budget.max_steps - self._steps_used),
        )
        # Never inherit cloud credentials, proxy credentials, or unrelated
        # host state.  The existing probe environment intentionally contains
        # only interpreter/runtime variables and public workspace paths.
        self._process = subprocess.Popen(
            _sandboxed_python_command(
                public_workspace=self.public_workspace,
                workspace=self.workspace,
                arguments=("-I", "-B", "-u", "-c", _PERSISTENT_BOOTSTRAP),
            ),
            # AA1 file-workspace semantics require ``read_file``/``write_file``
            # paths and ordinary relative Python paths to resolve from the
            # same phase workspace.  Public assets stay available through
            # their absolute environment paths and staged import roots.
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return restarted

    def _lose_process(self) -> None:
        """Discard a wedged worker without replenishing its physics budget."""

        # A killed or desynchronised child cannot report how many native
        # MuJoCo steps completed. Conservatively exhaust the phase budget; the
        # replacement remains useful for file import/build checks but cannot
        # gain another physical-control allowance.
        self._steps_used = self.budget.max_steps
        self._restart_pending = True
        self._stop_process()

    def _ensure_process(self) -> bool:
        process = self._process
        if (
            process is not None
            and process.poll() is None
            and process.stdin is not None
            and process.stdout is not None
        ):
            return False
        if process is not None:
            self._lose_process()
        return self._start_process()

    @property
    def calls(self) -> int:
        return self._calls

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_process()

    def __del__(self) -> None:  # pragma: no cover - best-effort interpreter cleanup
        self._stop_process()

    def execute(self, code: str, *, invalidate_driver: bool = False) -> dict[str, Any]:
        if self._closed:
            raise ProbeError("persistent execute_python session is closed")
        if not isinstance(code, str) or not code.strip():
            raise ProbeError("execute_python.code must be non-empty Python source")
        audit_public_source(
            code,
            condition=self.condition,
            allow_probe_utilities=True,
        )
        session_restarted = self._ensure_process()
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise ProbeError("persistent execute_python session is unavailable")
        self._calls += 1
        started = time.monotonic()
        request = json.dumps(
            {"code": code, "invalidate_driver": bool(invalidate_driver)},
            ensure_ascii=True,
        ).encode("utf-8") + b"\n"
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._lose_process()
            raise ProbeError(f"persistent execute_python transport failed: {exc}") from exc

        ready, _, _ = select.select([process.stdout], [], [], self.budget.timeout_s)
        if not ready:
            self._lose_process()
            return {
                "ok": False,
                "successful": False,
                "timed_out": True,
                "exit_code": None,
                "spawn_error": None,
                "stdout": "",
                "stderr": "",
                "physics_steps": None,
                "physics_steps_total": self._steps_used,
                "physics_step_budget_exhausted": True,
                "elapsed_wall_s": time.monotonic() - started,
                "session_restarted": session_restarted,
                "session_lost": True,
            }
        try:
            line = process.stdout.readline()
        except OSError as exc:
            self._lose_process()
            raise ProbeError(f"persistent execute_python read failed: {exc}") from exc
        if not line:
            exit_code = process.poll()
            stderr = b""
            if process.stderr is not None:
                try:
                    stderr = process.stderr.read()
                except OSError:
                    pass
            self._lose_process()
            return {
                "ok": False,
                "successful": False,
                "timed_out": False,
                "exit_code": exit_code,
                "spawn_error": "persistent execute_python process exited",
                "stdout": "",
                "stderr": stderr.decode("utf-8", errors="replace")[-self.budget.max_output_chars :],
                "physics_steps": None,
                "physics_steps_total": self._steps_used,
                "physics_step_budget_exhausted": True,
                "elapsed_wall_s": time.monotonic() - started,
                "session_restarted": session_restarted,
                "session_lost": True,
            }
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text.startswith(_PERSISTENT_RESULT_PREFIX):
            exit_code = process.poll()
            self._lose_process()
            return {
                "ok": False,
                "successful": False,
                "timed_out": False,
                "exit_code": exit_code,
                "spawn_error": "persistent execute_python protocol error",
                "stdout": "",
                "stderr": text[: self.budget.max_output_chars],
                "physics_steps": None,
                "physics_steps_total": self._steps_used,
                "physics_step_budget_exhausted": True,
                "elapsed_wall_s": time.monotonic() - started,
                "session_restarted": session_restarted,
                "session_lost": True,
            }
        try:
            payload = json.loads(text.split("=", 1)[1])
        except json.JSONDecodeError as exc:
            self._lose_process()
            raise ProbeError("persistent execute_python returned malformed JSON") from exc
        if not isinstance(payload, Mapping):
            self._lose_process()
            raise ProbeError("persistent execute_python response must be an object")
        stdout_value = payload.get("stdout", "")
        stderr_value = payload.get("stderr", "")
        stdout, stdout_truncated, stdout_total = _bounded_text(
            str(stdout_value).encode("utf-8", errors="replace"),
            self.budget.max_output_chars,
        )
        remaining = max(0, self.budget.max_output_chars - len(stdout))
        stderr, stderr_truncated, stderr_total = _bounded_text(
            str(stderr_value).encode("utf-8", errors="replace"), remaining
        )
        ok = bool(payload.get("ok"))
        error = payload.get("error")
        worker_steps_total = payload.get("physics_steps_total")
        if (
            isinstance(worker_steps_total, int)
            and not isinstance(worker_steps_total, bool)
            and worker_steps_total >= 0
        ):
            self._steps_used = min(
                self.budget.max_steps,
                max(self._steps_used, self._worker_step_offset + worker_steps_total),
            )
        return {
            "ok": ok,
            "successful": ok,
            "timed_out": False,
            "exit_code": 0 if ok else 1,
            "spawn_error": None,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_chars": stdout_total,
            "stderr_chars": stderr_total,
            "output_truncated": stdout_truncated or stderr_truncated,
            "elapsed_wall_s": time.monotonic() - started,
            "physics_steps": payload.get("physics_steps"),
            "physics_steps_total": self._steps_used,
            "physics_step_budget_exhausted": self._steps_used >= self.budget.max_steps,
            "error": error,
            "session_restarted": session_restarted,
            "session_lost": False,
        }


_NETWORK_IMPORT_ROOTS = frozenset(
    {
        "socket",
        "urllib",
        "requests",
        "httpx",
        "ftplib",
        "telnetlib",
        "subprocess",
        "importlib",
        "pkgutil",
        "runpy",
        "ctypes",
    }
)
_FILESYSTEM_INTROSPECTION_ROOTS = frozenset(
    {"os", "pathlib", "glob", "shutil", "tempfile", "fileinput", "sys", "inspect", "types"}
)
_DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
_OS_PROCESS_CALLS = frozenset(
    {
        "fork",
        "forkpty",
        "kill",
        "killpg",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "startfile",
        "system",
    }
)
_FORBIDDEN_PATH_LITERALS = (
    "/tasks/private",
    "\\tasks\\private",
    "/reference/",
    "\\reference\\",
    "autoadapter2.harness",
    "autoadapter2/harness",
)
_RESOURCE_FILE_TAGS = frozenset({"mesh", "texture", "hfield", "skin", "model", "sdf"})
_TEXT_SUFFIXES = frozenset({".xml", ".json", ".txt", ".md", ".py", ".yaml", ".yml"})
_PUBLIC_OBSERVATION_PREFIX = "__AUTOADAPTER_PUBLIC_OBSERVATION__="


def _module_path(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def audit_public_source(
    source: str,
    *,
    condition: ProbeCondition | str,
    allow_probe_utilities: bool = False,
) -> None:
    """Reject probe/candidate source that can reach private Framework code."""

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise ProbeSourceError(f"unknown source-audit condition {condition!r}")
    try:
        tree = ast.parse(source, filename="probe_or_driver.py")
    except SyntaxError as exc:
        raise ProbeSourceError(f"source is not valid Python: {exc.msg}") from exc

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            if node.level:
                errors.append("relative imports are forbidden")
        else:
            modules = []
        for module in modules:
            root = module.split(".", 1)[0]
            normal = module.lower()
            if root in _NETWORK_IMPORT_ROOTS:
                errors.append(f"network/process import is forbidden: {module}")
            if root in _FILESYSTEM_INTROSPECTION_ROOTS and not (
                allow_probe_utilities and root in {"os", "pathlib"}
            ):
                errors.append(f"filesystem/introspection import is forbidden: {module}")
            if root == "autoadapter2":
                allowed = condition == "skeleton-assisted" and normal.startswith(
                    "autoadapter2.trusted_skeletons"
                )
                if not allowed:
                    errors.append(
                        "autoadapter2 imports are limited to trusted_skeletons in skeleton-assisted mode"
                    )
                if any(part in normal.split(".") for part in ("harness", "reference", "private")):
                    errors.append(f"private Framework import is forbidden: {module}")
            if any(marker in normal for marker in ("harness", "reference")):
                errors.append(f"private/reference module access is forbidden: {module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DYNAMIC_CALLS:
                errors.append(f"dynamic execution is forbidden: {node.func.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_path = _module_path(node.func).lower()
            call_root, _, call_name = call_path.rpartition(".")
            if call_root == "os" and (
                call_name in _OS_PROCESS_CALLS
                or call_name.startswith("spawn")
                or call_name.startswith("exec")
            ):
                errors.append(f"process creation is forbidden: {call_path}")
        if isinstance(node, ast.Attribute):
            path = _module_path(node).lower()
            if any(part in path.split(".") for part in ("harness", "reference", "private_dir")):
                errors.append(f"private/reference attribute access is forbidden: {path}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.lower()
            if any(marker in value for marker in _FORBIDDEN_PATH_LITERALS):
                errors.append("private/reference path literal is forbidden")
            if value.strip().lower() in {"private", "reference", "harness"}:
                errors.append("private/reference directory literal is forbidden")
    if errors:
        raise ProbeSourceError("; ".join(dict.fromkeys(errors)))


def _inside_assets(path: Path, assets_root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(assets_root.resolve())
    except ValueError as exc:
        raise ProbeError(f"MJCF closure escapes assets/: {path}") from exc
    if not resolved.is_file():
        raise ProbeError(f"MJCF closure file is missing: {resolved}")
    return resolved


def _referenced_file(
    value: str,
    *,
    xml_path: Path,
    assets_root: Path,
    resource_dir: str | None = None,
) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ProbeError(f"absolute MJCF resource path is forbidden: {value}")
    candidates: list[Path] = []
    if resource_dir:
        resource_path = Path(resource_dir)
        if resource_path.is_absolute():
            raise ProbeError(f"absolute MJCF resource directory is forbidden: {resource_dir}")
        candidates.append(xml_path.parent / resource_path / relative)
    candidates.extend((xml_path.parent / relative, assets_root / relative))
    for candidate in candidates:
        if candidate.exists():
            return _inside_assets(candidate, assets_root)
    raise ProbeError(f"MJCF resource does not resolve inside assets/: {value}")


def resolve_asset_closure(package: RobotPackage) -> tuple[Path, ...]:
    """Resolve only the canonical MJCF include/resource transitive closure."""

    assets_root = (package.root / "assets").resolve()
    entrypoint = _inside_assets(package.mjcf_path, assets_root)
    pending = [entrypoint]
    visited: set[Path] = set()
    ordered: list[Path] = []
    while pending:
        current = _inside_assets(pending.pop(0), assets_root)
        if current in visited:
            continue
        visited.add(current)
        ordered.append(current)
        if current.suffix.lower() != ".xml":
            continue
        try:
            root = ET.fromstring(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ET.ParseError) as exc:
            raise ProbeError(f"cannot parse canonical MJCF XML {current}") from exc
        compiler = next(
            (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "compiler"),
            None,
        )
        meshdir = compiler.get("meshdir") if compiler is not None else None
        texturedir = compiler.get("texturedir") if compiler is not None else None
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            value = element.get("file")
            if not value:
                continue
            if tag == "include":
                referenced = _referenced_file(value, xml_path=current, assets_root=assets_root)
            elif tag in _RESOURCE_FILE_TAGS:
                directory = texturedir if tag == "texture" else meshdir
                referenced = _referenced_file(
                    value,
                    xml_path=current,
                    assets_root=assets_root,
                    resource_dir=directory,
                )
            else:
                continue
            if referenced not in visited:
                pending.append(referenced)
    return tuple(ordered)


def public_asset_closure_manifest(package: RobotPackage) -> dict[str, Any]:
    """Return a JSON-safe manifest containing only the canonical asset closure."""

    assets_root = (package.root / "assets").resolve()
    closure = resolve_asset_closure(package)
    files: list[dict[str, Any]] = []
    for path in closure:
        relative = path.relative_to(assets_root).as_posix()
        item: dict[str, Any] = {"path": relative, "size_bytes": path.stat().st_size}
        if path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                item["text"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                item["text_unavailable"] = True
        files.append(item)
    entrypoint = _inside_assets(package.mjcf_path, assets_root)
    return {
        "root": "assets",
        "entrypoint": entrypoint.relative_to(assets_root).as_posix(),
        "entrypoint_text": entrypoint.read_text(encoding="utf-8"),
        "files": files,
    }


_BOOTSTRAP = _RUNTIME_GUARD_BOOTSTRAP + r'''
max_steps = int(os.environ["AUTOADAPTER_PROBE_MAX_STEPS"])
max_sim_time_s = float(os.environ["AUTOADAPTER_PROBE_MAX_SIM_TIME_S"])
step_count = [0]
original_mj_step = mujoco.mj_step
python_root = os.environ.get("AUTOADAPTER_PROBE_PYTHON_ROOT")
if python_root:
    sys.path.insert(0, python_root)
candidate_root = os.environ.get("AUTOADAPTER_PROBE_CANDIDATE_ROOT")
if candidate_root:
    sys.path.insert(0, candidate_root)


def requested_steps(args, kwargs):
    value = kwargs.get("nstep", args[0] if args else 1)
    try:
        steps = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("probe nstep must be an integer") from exc
    if steps < 0:
        raise RuntimeError("probe nstep must not be negative")
    return steps


def bounded_mj_step(model, data, *args, **kwargs):
    steps = requested_steps(args, kwargs)
    if step_count[0] + steps > max_steps:
        raise RuntimeError("probe control-step budget exceeded")
    result = original_mj_step(model, data, *args, **kwargs)
    step_count[0] += steps
    if float(data.time) > max_sim_time_s:
        raise RuntimeError("probe simulated-time budget exceeded")
    return result


mujoco.mj_step = bounded_mj_step
try:
    with open(sys.argv[1], "r", encoding="utf-8") as script_file:
        script_source = script_file.read()
    _compile_authorised = True
    try:
        script_code = compile(script_source, sys.argv[1], "exec")
    finally:
        _compile_authorised = False
    exec(script_code, {"__name__": "__main__", "__file__": sys.argv[1]})
finally:
    print("__AUTOADAPTER_PROBE_FACTS__=" + json.dumps({"physics_steps": step_count[0]}))
'''


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ProbeError(f"public probe input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        try:
            clone = subprocess.run(
                ["cp", "-c", "-p", os.fspath(source), os.fspath(destination)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
        else:
            if clone.returncode == 0:
                return
    shutil.copy2(source, destination)


def _stage_trusted_python_root(workspace: Path, framework_source_root: Path) -> Path:
    """Stage only the trusted skeleton modules and their tiny contract dependency."""

    source_package = framework_source_root / "autoadapter2"
    trusted_source = source_package / "trusted_skeletons"
    contract_source = source_package / "driver_synthesis" / "skeleton_contract.py"
    if not trusted_source.is_dir() or not contract_source.is_file():
        raise ProbeError("trusted skeleton source is unavailable for skeleton-assisted probe")
    destination = workspace / "public_python"
    destination.mkdir(parents=True, exist_ok=True)
    package_root = destination / "autoadapter2"
    (package_root / "trusted_skeletons").mkdir(parents=True, exist_ok=True)
    (package_root / "driver_synthesis").mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text(
        '"""Minimal probe-visible AutoAdapter namespace."""\n',
        encoding="utf-8",
    )
    (package_root / "driver_synthesis" / "__init__.py").write_text(
        "from .skeleton_contract import (\n"
        "    INFRASTRUCTURE_NAMES, PrimitiveDescription, SessionBoundSkeleton,\n"
        "    SkeletonContractError, discover_primitives, validate_capability_names,\n"
        "    validate_explicit_capability_methods,\n"
        ")\n",
        encoding="utf-8",
    )
    _copy_file(contract_source, package_root / "driver_synthesis" / "skeleton_contract.py")
    for source in sorted(trusted_source.glob("*.py")):
        if source.is_file():
            _copy_file(source, package_root / "trusted_skeletons" / source.name)
    data_source = trusted_source / "data"
    if data_source.is_dir():
        for source in sorted(data_source.rglob("*")):
            if source.is_file():
                _copy_file(
                    source,
                    package_root
                    / "trusted_skeletons"
                    / "data"
                    / source.relative_to(data_source),
                )
    for forbidden in (
        package_root / "harness",
        package_root / "reference",
        package_root / "libraries",
        package_root / "validation_compiler",
    ):
        if forbidden.exists():
            raise ProbeError(f"forbidden Framework module was staged: {forbidden}")
    return destination


def _asset_entrypoint(package: RobotPackage) -> str:
    assets_root = (package.root / "assets").resolve()
    try:
        return package.mjcf_path.resolve().relative_to(assets_root).as_posix()
    except ValueError as exc:
        raise ProbeError("canonical MJCF entrypoint must be inside package assets") from exc


def prepare_public_probe_workspace(
    package: RobotPackage,
    workspace: str | Path,
    *,
    condition: ProbeCondition | str,
    framework_source_root: str | Path | None = None,
) -> PublicProbeWorkspace:
    """Stage only public package files for a condition-local probe.

    ``workspace`` is owned by the generation condition.  Existing files are
    never treated as an admitted package root; in particular a pre-existing
    ``tasks/private`` or ``reference`` directory makes staging fail closed.
    """

    if condition not in {"skeleton-assisted", "from-scratch"}:
        raise ProbeError(f"unknown probe condition {condition!r}")
    workspace_path = Path(workspace).resolve()
    package_root = package.root.resolve()
    try:
        workspace_path.relative_to(package_root)
        raise ProbeError("probe workspace must not be inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(workspace_path)
        raise ProbeError("probe workspace must not contain the admitted package root")
    except ValueError:
        pass
    if framework_source_root is not None:
        framework_root = Path(framework_source_root).resolve()
        try:
            framework_root.relative_to(package_root)
            raise ProbeError("trusted staging source must not be inside the admitted package root")
        except ValueError:
            pass
        try:
            package_root.relative_to(framework_root)
            raise ProbeError("trusted staging source must not contain the admitted package root")
        except ValueError:
            pass
    destination = workspace_path / "public_package"
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "tasks" / "private").exists() or (destination / "reference").exists():
        raise ProbeError("public probe workspace already contains a private/reference directory")

    assets_source = (package.root / "assets").resolve()
    if not assets_source.is_dir():
        raise ProbeError(f"package assets directory is missing: {assets_source}")
    for source in resolve_asset_closure(package):
        relative = source.relative_to(assets_source)
        _copy_file(source, destination / "assets" / relative)

    _copy_file(package.root / "morphology.json", destination / "morphology.json")
    _copy_file(package.root / "tasks" / "catalog.json", destination / "tasks" / "catalog.json")
    _copy_file(package.root / "tasks" / "sources.json", destination / "tasks" / "sources.json")

    skeleton_root: Path | None = None
    python_root: Path | None = None
    if condition == "skeleton-assisted":
        skeleton_root = destination / "skeleton"
        if not package.skeleton_dir.is_dir():
            raise ProbeError("skeleton-assisted probe requires package skeleton source")
        for source in sorted(package.skeleton_dir.rglob("*")):
            if source.is_file():
                _copy_file(source, skeleton_root / source.relative_to(package.skeleton_dir))
        if framework_source_root is None:
            raise ProbeError("skeleton-assisted probe needs trusted skeleton source root")
        python_root = _stage_trusted_python_root(
            destination.parent,
            Path(framework_source_root).resolve(),
        )

    if (destination / "tasks" / "private").exists() or (destination / "reference").exists():
        raise ProbeError("private/reference files leaked into public probe workspace")
    scene_path = destination / "assets" / Path(_asset_entrypoint(package))
    if not scene_path.is_file():
        raise ProbeError("staged public canonical scene is missing")
    return PublicProbeWorkspace(
        root=destination,
        scene_path=scene_path,
        skeleton_root=skeleton_root,
        python_root=python_root,
    )


def _normalise_requests(
    requests: Sequence[ProbeRequest | Mapping[str, Any]],
    *,
    budget: ProbeBudget,
) -> tuple[ProbeRequest, ...]:
    if budget.max_requests is not None and len(requests) > budget.max_requests:
        raise ProbeError(
            f"probe request count {len(requests)} exceeds budget {budget.max_requests}"
        )
    normalised: list[ProbeRequest] = []
    seen: set[str] = set()
    for index, value in enumerate(requests):
        request = value if isinstance(value, ProbeRequest) else ProbeRequest.from_mapping(value, index=index)
        if request.probe_id in seen:
            raise ProbeError(f"duplicate probe_id {request.probe_id!r}")
        if not request.probe_id.replace("_", "").replace("-", "").isalnum():
            raise ProbeError(f"probe_id {request.probe_id!r} contains unsafe filename characters")
        seen.add(request.probe_id)
        normalised.append(request)
    return tuple(normalised)


def _bounded_text(value: bytes, limit: int) -> tuple[str, bool, int]:
    decoded = value.decode("utf-8", errors="replace")
    if len(decoded) <= limit:
        return decoded, False, len(decoded)
    return decoded[:limit] + "\n<probe output truncated>", True, len(decoded)


def _probe_environment(
    *,
    public_workspace: PublicProbeWorkspace,
    workspace: Path,
    budget: ProbeBudget,
    max_steps: int | None = None,
) -> dict[str, str]:
    effective_max_steps = budget.max_steps if max_steps is None else int(max_steps)
    if effective_max_steps < 0:
        raise ProbeError("remaining execute_python step budget must not be negative")
    environment: dict[str, str] = {}
    mujoco_gl = os.environ.get("MUJOCO_GL")
    if mujoco_gl in {"glfw", "egl", "osmesa", "cgl", "disable"}:
        environment["MUJOCO_GL"] = mujoco_gl
    temporary_root = workspace.resolve() / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "TMPDIR": str(temporary_root),
            "PYTHONNOUSERSITE": "1",
            "AUTOADAPTER_PROBE_WORKSPACE": str(workspace.resolve()),
            "AUTOADAPTER_PROBE_PUBLIC_PACKAGE": str(public_workspace.root),
            "AUTOADAPTER_PROBE_SCENE": str(public_workspace.scene_path),
            "AUTOADAPTER_PROBE_MAX_STEPS": str(effective_max_steps),
            "AUTOADAPTER_PROBE_MAX_SIM_TIME_S": str(budget.max_sim_time_s),
        }
    )
    if public_workspace.skeleton_root is not None:
        environment["AUTOADAPTER_PROBE_SKELETON_ROOT"] = str(public_workspace.skeleton_root)
    if public_workspace.python_root is not None:
        environment["AUTOADAPTER_PROBE_PYTHON_ROOT"] = str(public_workspace.python_root)
    if (public_workspace.root / "driver.py").is_file():
        environment["AUTOADAPTER_PROBE_CANDIDATE_ROOT"] = str(public_workspace.root)
    return environment


def _run_one(
    request: ProbeRequest,
    *,
    public_workspace: PublicProbeWorkspace,
    workspace: Path,
    budget: ProbeBudget,
) -> dict[str, Any]:
    audit_public_source(
        request.script,
        condition="skeleton-assisted" if public_workspace.python_root else "from-scratch",
        allow_probe_utilities=True,
    )
    probe_dir = workspace / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    script_path = probe_dir / f"{request.probe_id}.py"
    script_path.write_text(request.script, encoding="utf-8")
    command = _sandboxed_python_command(
        public_workspace=public_workspace,
        workspace=workspace,
        arguments=("-I", "-B", "-u", "-c", _BOOTSTRAP, str(script_path)),
    )
    started = time.monotonic()
    timed_out = False
    spawn_error: str | None = None
    stdout_bytes = b""
    stderr_bytes = b""
    exit_code: int | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(public_workspace.root),
            env=_probe_environment(
                public_workspace=public_workspace,
                workspace=workspace,
                budget=budget,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = process.communicate(timeout=budget.timeout_s)
        exit_code = process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        if process is not None:
            process.kill()
            stdout_bytes, stderr_bytes = process.communicate()
        else:
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
    except OSError as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    stdout, stdout_truncated, stdout_total = _bounded_text(stdout_bytes, budget.max_output_chars)
    remaining = max(0, budget.max_output_chars - len(stdout))
    stderr, stderr_truncated, stderr_total = _bounded_text(stderr_bytes, remaining)
    physics_steps: int | None = None
    public_observation: dict[str, Any] | None = None
    clean_stdout_lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("__AUTOADAPTER_PROBE_FACTS__="):
            try:
                facts = json.loads(line.split("=", 1)[1])
                if isinstance(facts, Mapping) and isinstance(facts.get("physics_steps"), int):
                    physics_steps = int(facts["physics_steps"])
            except json.JSONDecodeError:
                pass
        elif line.startswith(_PUBLIC_OBSERVATION_PREFIX):
            try:
                observation = json.loads(line.split("=", 1)[1])
                if isinstance(observation, dict):
                    public_observation = observation
            except json.JSONDecodeError:
                pass
        else:
            clean_stdout_lines.append(line)
    return {
        "probe_id": request.probe_id,
        "script_relpath": f"probes/{request.probe_id}.py",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "spawn_error": spawn_error,
        "stdout": "\n".join(clean_stdout_lines),
        "stderr": stderr,
        "stdout_chars": stdout_total,
        "stderr_chars": stderr_total,
        "output_truncated": stdout_truncated or stderr_truncated,
        "elapsed_wall_s": elapsed,
        "step_budget": budget.max_steps,
        "simulated_time_budget_s": budget.max_sim_time_s,
        "physics_steps": physics_steps,
        "public_observation": public_observation,
    }


def run_probes(
    requests: Sequence[ProbeRequest | Mapping[str, Any]],
    *,
    package: RobotPackage,
    workspace: str | Path,
    condition: ProbeCondition | str,
    budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
    candidate_source: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """Execute bounded model-requested scripts in a public-only workspace."""

    normalised = _normalise_requests(requests, budget=budget)
    if not normalised:
        return ()
    condition_name = str(condition)
    workspace_path = Path(workspace).resolve()
    package_root = package.root.resolve()
    try:
        workspace_path.relative_to(package_root)
        raise ProbeError("probe workspace must not be inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(workspace_path)
        raise ProbeError("probe workspace must not contain the admitted package root")
    except ValueError:
        pass
    root = Path(source_root).resolve() if source_root is not None else Path(__file__).resolve().parents[2]
    try:
        root.relative_to(package_root)
        raise ProbeError("probe staging source must not point inside the admitted package root")
    except ValueError:
        pass
    try:
        package_root.relative_to(root)
        raise ProbeError("probe staging source must not contain the admitted package root")
    except ValueError:
        pass
    public_workspace = prepare_public_probe_workspace(
        package,
        workspace_path,
        condition=condition_name,
        framework_source_root=root,
    )
    candidate_path = public_workspace.root / "driver.py"
    if candidate_source is None:
        candidate_path.unlink(missing_ok=True)
    else:
        if not isinstance(candidate_source, str) or not candidate_source.strip():
            raise ProbeError("candidate_source must be non-empty Python source")
        audit_public_source(candidate_source, condition=condition_name)
        candidate_path.write_text(candidate_source, encoding="utf-8")
    return tuple(
        _run_one(
            request,
            public_workspace=public_workspace,
            workspace=workspace_path,
            budget=budget,
        )
        for request in normalised
    )


def probe(
    request: ProbeRequest | Mapping[str, Any],
    *,
    package: RobotPackage,
    workspace: str | Path,
    condition: ProbeCondition | str,
    budget: ProbeBudget = ProbeBudget(),
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for one local development probe."""

    return run_probes(
        [request],
        package=package,
        workspace=workspace,
        condition=condition,
        budget=budget,
        source_root=source_root,
    )[0]


__all__ = [
    "ProbeBudget",
    "ProbeCondition",
    "ProbeError",
    "ProbeRequest",
    "ProbeSourceError",
    "PersistentPythonSession",
    "PublicProbeWorkspace",
    "audit_public_source",
    "prepare_public_probe_workspace",
    "probe",
    "run_probes",
]
