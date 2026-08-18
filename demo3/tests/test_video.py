from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from autoadapter2.harness.video import VideoError, encode_rgb_video, inspect_video


class VideoTests(unittest.TestCase):
    def test_encode_and_inspect_real_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trial.mp4"
            frames = [
                np.full((8, 10, 3), value, dtype=np.uint8)
                for value in (0, 64, 128, 255)
            ]

            result = encode_rgb_video(frames, output, fps=5)
            info = inspect_video(result)

            self.assertEqual(result, output)
            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(info.frame_count, len(frames))
            self.assertEqual(info.width, 10)
            self.assertEqual(info.height, 8)
            self.assertGreater(info.duration, 0.0)
            self.assertTrue(info.decodable)
            self.assertTrue(info.complete)

    def test_zero_frames_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "empty.mp4"
            with self.assertRaisesRegex(VideoError, "zero frames"):
                encode_rgb_video([], output, fps=5)


if __name__ == "__main__":
    unittest.main()
