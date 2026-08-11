"""Explicit real Linux SDK2/CycloneDDS/MuJoCo test; prerequisites never skip."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from autoadapter2.integrations.unitree_go2.readiness import CHECK_IDS, run_readiness


ROOT = Path(__file__).resolve().parents[1]


def test_real_unitree_sdk2_dds_mujoco_six_check_route() -> None:
    assert sys.platform == "linux", "formal readiness must run on Linux"
    runtime_lock = Path(os.environ["AUTOADAPTER_RUNTIME_LOCK"])
    model = Path(os.environ["AUTOADAPTER_GO2_MODEL"])
    reference_root = Path(os.environ["AUTOADAPTER_REFERENCE_ROOT"])
    report_path = Path(os.environ["AUTOADAPTER_READINESS_REPORT"])
    report = run_readiness(
        run_id="linux-real-unitree-go2-route",
        manifest_path=ROOT / "integrations/unitree-go2/integration_manifest.json",
        profile_path=ROOT / "contracts/profiles/readiness/general-demo-integration-readiness/1.0.0/profile.json",
        runtime_lock_path=runtime_lock,
        model_path=model,
        report_path=report_path,
        reference_root=reference_root,
    )
    assert [item["check_id"] for item in report["checks"]] == list(CHECK_IDS)
    assert all(item["verdict"] == "PASS" for item in report["checks"]), report
    assert report["cleanup"]["verdict"] == "PASS", report
    assert report["verdict"] == "PASS", report
