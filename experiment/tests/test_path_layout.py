from __future__ import annotations

import unittest
from pathlib import Path

from experiment.path_layout import REPOSITORY_ROOT, resolve_run_path


class PathLayoutTests(unittest.TestCase):
    def test_existing_path_is_unchanged(self) -> None:
        path = Path("experiment/experiment1a_generation/manifest.json")
        self.assertEqual(resolve_run_path(path), path)

    def test_old_b1_scheduler_maps_to_archive(self) -> None:
        old = Path(
            "experiment/experiment1/runs/"
            "readiness-m2-aloha-output32k-rev0117-20260822a/scheduler_record.json"
        )
        resolved = resolve_run_path(old)
        self.assertEqual(
            resolved,
            Path(
                "experiment/archive/runs/experiment1/"
                "readiness-m2-aloha-output32k-rev0117-20260822a/scheduler_record.json"
            ),
        )
        self.assertTrue((REPOSITORY_ROOT / resolved).is_file())

    def test_old_b2_report_maps_to_archive_for_absolute_path(self) -> None:
        relative = Path(
            "experiment/b2_recap/runs/"
            "m2-opus5-pick-place-a6-356c400/report.json"
        )
        old = REPOSITORY_ROOT / relative
        resolved = resolve_run_path(old)
        self.assertEqual(
            resolved,
            REPOSITORY_ROOT
            / "experiment/archive/runs/b2_recap/"
            "m2-opus5-pick-place-a6-356c400/report.json",
        )
        self.assertTrue(resolved.is_file())

    def test_existing_archive_path_and_external_path_are_unchanged(self) -> None:
        archived = Path(
            "experiment/archive/runs/b2_recap/"
            "m2-opus5-pick-place-a6-356c400/report.json"
        )
        self.assertEqual(resolve_run_path(archived), archived)
        external = Path("/tmp/not-an-experiment-run/report.json")
        self.assertEqual(resolve_run_path(external), external)


if __name__ == "__main__":
    unittest.main()
