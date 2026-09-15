"""Run one experiment DEMO using mainline ReCAP/MCP and local task criteria."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "AA1"))

from task_scoring import install_experiment_scoring


def main():
    # Only this experiment process extends the existing evaluator dispatch.
    # AA1 source files and its configured fixed DEMOs are unchanged.
    install_experiment_scoring()
    # The SDK's 2 s exit grace interrupted video encoding before the runtime
    # report was written in the GPT-6 ReCAP diagnostic. Let recording finish;
    # this changes shutdown only, not task, model, or simulation budgets.
    import mcp.client.stdio
    mcp.client.stdio.PROCESS_TERMINATION_TIMEOUT = 30.0
    from auto_adapter.agent.recap import main as recap_main
    return recap_main()


if __name__ == "__main__":
    raise SystemExit(main())
