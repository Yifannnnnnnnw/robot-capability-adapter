# SPDX-License-Identifier: Apache-2.0
"""Vision perception tool — render the MuJoCo scene + ask Claude to identify
objects. Replaces `get_object_position` (which reads ground truth) as the
agent's way to find things in the world.

Workflow:
    1. Skeleton renders the scene (one or several camera angles).
    2. We base64-encode the image(s).
    3. Call Claude (multimodal) with the image + a structured prompt:
       "List visible objects. For each, give a guess of its world position
        and a short description (color/shape)."
    4. Return the parsed list to the agent.

The position estimates are coarse — they're VLM guesses, not measurements.
Real perception would use a calibrated camera + depth + detection model.
But for the paper claim "agent can plan without ground truth", a VLM
perception step is enough.
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .react_loop import ToolSpec


_VISION_SYSTEM = """\
You are a vision perception module for a robot arm. You are given a rendered \
image of the robot's workspace and the camera's approximate viewing position. \
Your job: identify each visible object that the arm could plausibly grasp \
or interact with, give a short description (color, shape, approximate size), \
and give a coarse 3-D world-frame position estimate as floats.

Output STRICT JSON only — no prose before or after. The JSON schema is:
  {
    "objects": [
      {
        "name": "<short identifier you assign>",
        "description": "<color/shape/size 1-line>",
        "world_xyz_estimate": [<x>, <y>, <z>],
        "confidence": <0.0-1.0>
      },
      ...
    ],
    "scene_summary": "<one-sentence overall scene description>"
  }

Important constraints:
  - Only include objects on the table / in the scene, NOT the arm itself.
  - Position estimates: world frame, meters. If you can't tell, estimate based \
on perspective + the scene_summary hint provided in the user message.
  - confidence: how sure are you that this object exists and your position is \
roughly right. Robotic objects are typically near z=0.0–0.1m on a table.
  - If the scene is empty (no graspable bodies visible), return an empty list \
for "objects" and explain in "scene_summary".
"""


def _encode_image_base64(frame: np.ndarray) -> str:
    """Encode a (H, W, 3) uint8 RGB array as a base64 PNG."""
    from PIL import Image  # noqa: PLC0415

    img = Image.fromarray(frame.astype("uint8"))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _parse_perception_json(text: str) -> dict:
    """Salvage JSON from the model's reply, even if wrapped in code fences."""
    # Strip ```json ... ``` fences if present
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    text = text.strip()
    # If the text starts with non-{, find the first {
    if not text.startswith("{"):
        i = text.find("{")
        if i >= 0:
            text = text[i:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Final fallback: return the raw text wrapped
        return {"objects": [], "scene_summary": f"(parse failed) {text[:200]}",
                "_parse_error": True}


def make_look_at_scene_tool(
    skel,
    *,
    bedrock_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region: str = "us-east-1",
    cameras: tuple[str | int, ...] = (-1,),
    height: int = 480,
    width: int = 640,
    save_dir: Optional[Path] = None,
    scene_hint: str = "",
    call_log: Optional[list] = None,
) -> ToolSpec:
    """Build a `look_at_scene()` ToolSpec bound to a specific skeleton + Claude.

    Each call renders the scene from `cameras` (default = the skeleton's
    default free camera), sends to Claude vision, returns parsed perception.
    Saves the most recent rendered image to `save_dir/last_view.png` for
    debugging.

    `scene_hint` is a short caption appended to the user prompt — e.g.
    "tabletop with 6 graspable objects: banana, mug, bottle, screwdriver,
    duck, lego" — to anchor the model's guesses. Optional.
    """
    if save_dir is not None:
        save_dir = Path(save_dir).resolve()
        save_dir.mkdir(parents=True, exist_ok=True)

    def _handler(inp: dict) -> dict:
        from anthropic import AnthropicBedrock  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        client = AnthropicBedrock(aws_region=region)
        _t0 = _time.time()

        # Render the scene
        frames = []
        for cam in cameras:
            try:
                f = skel.render(camera=cam, height=height, width=width)
            except TypeError:
                # some skeletons may not support camera kwarg yet — fall back
                f = skel.render()
            frames.append((cam, f))

        # Save the most recent image for human debugging
        if save_dir is not None and frames:
            from PIL import Image  # noqa: PLC0415

            for cam, frame in frames:
                Image.fromarray(frame.astype("uint8")).save(
                    save_dir / f"look_at_scene_cam{cam}.png"
                )

        # Build vision message
        content_blocks: list[dict] = []
        for cam, frame in frames:
            b64 = _encode_image_base64(frame)
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })

        user_text = (
            f"Camera view(s) of the robot workspace. "
            f"{'Hint: ' + scene_hint if scene_hint else ''}"
            "\n\nReturn STRICT JSON per the schema (no prose, no fences)."
        )
        content_blocks.append({"type": "text", "text": user_text})

        try:
            resp = client.messages.create(
                model=bedrock_model,
                max_tokens=2000,
                system=_VISION_SYSTEM,
                messages=[{"role": "user", "content": content_blocks}],
            )
        except Exception as e:  # noqa: BLE001
            return {
                "objects": [],
                "scene_summary": f"vision API call failed: {type(e).__name__}: {e}",
                "_api_error": True,
            }

        text = "".join(b.text for b in resp.content if b.type == "text")
        parsed = _parse_perception_json(text)
        parsed["_token_usage"] = {
            "in": int(getattr(resp.usage, "input_tokens", 0)),
            "out": int(getattr(resp.usage, "output_tokens", 0)),
        }
        if call_log is not None:
            call_log.append({
                "tool": "look_at_scene",
                "input": inp,
                "ok": True,
                "dur_ms": (_time.time() - _t0) * 1000.0,
                "vision_tokens": parsed["_token_usage"],
                "n_objects_seen": len(parsed.get("objects", [])),
            })
        return parsed

    return ToolSpec(
        name="look_at_scene",
        description=(
            "Render the workspace from the camera and ask a vision model to "
            "identify objects + estimate their world positions. Returns "
            "{objects: [{name, description, world_xyz_estimate, confidence}, "
            "...], scene_summary, _token_usage}. Use this when you need to "
            "find an object visually rather than by name lookup. Positions "
            "are coarse VLM guesses — refine via move_cartesian + observation."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_handler,
    )
