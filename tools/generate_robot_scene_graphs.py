#!/usr/bin/env python3
"""Render MJCF body scene-graph PNGs from AA1's robot catalogue."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont

from aa1_rendering import add_robot_arguments, robot_scenes


@dataclass
class SceneNode:
    name: str
    kind: str = "body"
    joints: list[str] = field(default_factory=list)
    geom_count: int = 0
    site_count: int = 0
    camera_count: int = 0
    children: list["SceneNode"] = field(default_factory=list)
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    subtree_width: int = 0


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(path: Path) -> ElementTree.Element:
    try:
        return ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"cannot parse {path}: {exc}") from exc


def _inline_includes(element: ElementTree.Element, base_dir: Path, seen: set[Path]) -> None:
    children = list(element)
    for index, child in enumerate(children):
        if _local_name(child.tag) != "include":
            _inline_includes(child, base_dir, seen)
            continue
        include_file = child.attrib.get("file")
        if not include_file:
            continue
        include_path = (base_dir / include_file).resolve()
        if include_path in seen:
            raise RuntimeError(f"recursive MJCF include detected: {include_path}")
        included_root = _parse_xml(include_path)
        _inline_includes(included_root, include_path.parent, seen | {include_path})
        element.remove(child)
        for offset, included_child in enumerate(list(included_root)):
            element.insert(index + offset, included_child)


def _node_from_body(body: ElementTree.Element, fallback: str) -> SceneNode:
    node = SceneNode(name=body.attrib.get("name", fallback))
    for child_index, child in enumerate(list(body)):
        tag = _local_name(child.tag)
        if tag == "body":
            node.children.append(_node_from_body(child, f"body_{child_index}"))
        elif tag == "joint":
            node.joints.append(child.attrib.get("name", child.attrib.get("type", "joint")))
        elif tag == "freejoint":
            node.joints.append(child.attrib.get("name", "freejoint"))
        elif tag == "geom":
            node.geom_count += 1
        elif tag == "site":
            node.site_count += 1
        elif tag == "camera":
            node.camera_count += 1
    return node


def _scene_tree(entrypoint: Path) -> SceneNode:
    root_element = _parse_xml(entrypoint)
    _inline_includes(root_element, entrypoint.parent, {entrypoint.resolve()})
    scene = SceneNode("world", kind="world")
    body_index = 0
    for worldbody in root_element.iter():
        if _local_name(worldbody.tag) != "worldbody":
            continue
        for child in list(worldbody):
            tag = _local_name(child.tag)
            if tag == "body":
                scene.children.append(_node_from_body(child, f"body_{body_index}"))
                body_index += 1
            elif tag == "geom":
                scene.geom_count += 1
            elif tag == "site":
                scene.site_count += 1
            elif tag == "camera":
                scene.camera_count += 1
    return scene


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        candidate = Path(path)
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _label_lines(node: SceneNode) -> list[str]:
    facts = []
    if node.joints:
        facts.append("j:" + ",".join(node.joints[:3]) + ("..." if len(node.joints) > 3 else ""))
    counts = []
    if node.geom_count:
        counts.append(f"g{node.geom_count}")
    if node.site_count:
        counts.append(f"s{node.site_count}")
    if node.camera_count:
        counts.append(f"c{node.camera_count}")
    if counts:
        facts.append(" ".join(counts))
    return [node.name] + facts


def _measure_nodes(node: SceneNode, draw: ImageDraw.ImageDraw, name_font: ImageFont.ImageFont, meta_font: ImageFont.ImageFont) -> None:
    lines = _label_lines(node)
    widths = [_text_size(draw, lines[0], name_font)[0]]
    widths.extend(_text_size(draw, line, meta_font)[0] for line in lines[1:])
    node.width = max(96, min(260, max(widths) + 24))
    node.height = 34 + 16 * max(0, len(lines) - 1)
    for child in node.children:
        _measure_nodes(child, draw, name_font, meta_font)


def _layout(node: SceneNode, depth: int, left: int, gap_x: int, gap_y: int) -> None:
    if not node.children:
        node.subtree_width = node.width
    else:
        cursor = left
        total = 0
        for child in node.children:
            _layout(child, depth + 1, cursor, gap_x, gap_y)
            cursor += child.subtree_width + gap_x
            total += child.subtree_width
        total += gap_x * (len(node.children) - 1)
        node.subtree_width = max(node.width, total)
    node.x = left + node.subtree_width // 2
    node.y = 86 + depth * gap_y


def _walk(node: SceneNode) -> Iterable[SceneNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _draw_node(draw: ImageDraw.ImageDraw, node: SceneNode, name_font: ImageFont.ImageFont, meta_font: ImageFont.ImageFont) -> None:
    x0 = node.x - node.width // 2
    y0 = node.y - node.height // 2
    x1 = node.x + node.width // 2
    y1 = node.y + node.height // 2
    fill = "#eef6ff" if node.kind == "world" else "#f8fafc"
    outline = "#2563eb" if node.kind == "world" else "#64748b"
    draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=fill, outline=outline, width=2)
    lines = _label_lines(node)
    y = y0 + 8
    for index, line in enumerate(lines):
        font = name_font if index == 0 else meta_font
        color = "#0f172a" if index == 0 else "#475569"
        text = line if len(line) <= 34 else line[:31] + "..."
        text_width, text_height = _text_size(draw, text, font)
        draw.text((node.x - text_width // 2, y), text, fill=color, font=font)
        y += text_height + 5


def _render_png(tree: SceneNode, output_path: Path, title: str, entrypoint: str) -> None:
    scratch = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(scratch)
    title_font = _font(24)
    subtitle_font = _font(14)
    name_font = _font(13)
    meta_font = _font(11)
    _measure_nodes(tree, draw, name_font, meta_font)
    max_depth = max(_depth(node, tree) for node in _walk(tree))
    gap_x = 28
    gap_y = 96
    _layout(tree, 0, 40, gap_x, gap_y)
    nodes = list(_walk(tree))
    width = min(max(max(node.x + node.width // 2 for node in nodes) + 40, 900), 14000)
    height = 110 + max_depth * gap_y + 92
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((32, 22), title, fill="#0f172a", font=title_font)
    draw.text((32, 54), f"MJCF body scene graph from {entrypoint}", fill="#475569", font=subtitle_font)
    for node in nodes:
        for child in node.children:
            draw.line((node.x, node.y + node.height // 2, child.x, child.y - child.height // 2), fill="#94a3b8", width=2)
    for node in nodes:
        _draw_node(draw, node, name_font, meta_font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _depth(target: SceneNode, root: SceneNode, depth: int = 0) -> int:
    if target is root:
        return depth
    for child in root.children:
        child_depth = _depth(target, child, depth + 1)
        if child_depth >= 0:
            return child_depth
    return -1


def _write_scene_graph(robot_id: str, entrypoint: Path, output_dir: Path, aa1_root: Path) -> Path:
    tree = _scene_tree(entrypoint)
    output_path = output_dir / f"{robot_id}.png"
    _render_png(tree, output_path, robot_id, str(entrypoint.relative_to(aa1_root.resolve())))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    add_robot_arguments(parser, "scene_graphs")
    args = parser.parse_args()
    for robot_id, scene_path in robot_scenes(args.aa1_root, args.robot).items():
        output = _write_scene_graph(robot_id, scene_path, args.output_dir, args.aa1_root)
        print(f"{output} <- {scene_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
