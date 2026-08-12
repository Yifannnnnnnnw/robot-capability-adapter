# RobotStudio SO101 experimental source cache

This directory records exact small MJCF payloads from AutoAdapter 1.0 commit `585eb1f1fde33f17f5f9a1e169a18dd41f97b586`. The source entry is `assets/mjcf/robotstudio_so101/so101.xml`; task/scenes use `assets/mjcf/robotstudio_so101/scene_box.xml; assets/mjcf/robotstudio_so101/scene.xml`.

The XML files are vendored byte-for-byte. Meshes are intentionally not vendored; `asset_closure.json` lists every recursive include/mesh path and SHA-256 and marks the required upstream cache. License/source notice: Apache-2.0, as declared by the upstream source package.

Status: `HUMAN_REVIEW_REQUIRED`.
Route: `DIRECT_MUJOCO_EXPERIMENTAL`.
