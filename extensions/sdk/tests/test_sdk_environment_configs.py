import json
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[1]


def _environment(robot: str) -> tuple[dict[str, object], str]:
    directory = SDK_ROOT / "environments" / robot / "1.0.0"
    definition = json.loads((directory / "environment.json").read_text(encoding="utf-8"))
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (directory / "Dockerfile", directory / "README.md")
        if path.exists()
    )
    return definition, text


def test_so_arm101_environment_is_pinned_to_current_route() -> None:
    definition, text = _environment("so-arm101-linux-amd64")

    assert definition["platform"] == {
        "os": "Ubuntu 24.04",
        "architecture": "amd64",
        "python": "3.12",
    }
    assert [(package["name"], package["version"]) for package in definition["packages"]] == [
        ("lerobot", "0.6.0"),
        ("feetech-servo-sdk", "1.0.0"),
        ("mujoco", "3.3.6"),
    ]
    assert definition["packages"][0]["commit"] == "30da8e687a6dfc617fcd94afc367ac7071c376ce"
    assert definition["model"] == {
        "repository": "https://github.com/TheRobotStudio/SO-ARM100.git",
        "commit": "7629d2ad9853d10fb903093a33ef6114099d97e5",
        "path": "/opt/SO-ARM100/Simulation/SO101/so101_new_calib.xml",
    }
    assert definition["route"]["cli"] == "extensions/sdk/scripts/run_so_arm101_route_check.py"
    assert definition["route"]["module"] == "autoadapter2_sdk.so_arm101.route_check:run_route_check"
    assert "general_demo" not in text
    assert "autoadapter2.integrations" not in text
    assert "autoadapter2_sdk" in text
    assert "run_so_arm101_route_check.py" in text


def test_go2_environment_is_pinned_to_current_route() -> None:
    definition, text = _environment("unitree-go2-linux-amd64")

    assert definition["platform"] == {
        "os": "Ubuntu 22.04",
        "architecture": "amd64",
        "python": "3.10",
    }
    assert [(package["name"], package["version"]) for package in definition["packages"]] == [
        ("unitree_sdk2py", "1.0.1"),
        ("cyclonedds", "0.10.2"),
        ("mujoco", "3.3.6"),
    ]
    assert definition["packages"][0]["commit"] == "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5"
    assert definition["source_checkouts"] == [
        {
            "repository": "https://github.com/unitreerobotics/unitree_sdk2_python",
            "commit": "65691c8a8bc53b98d3976dba4dbf9d5d20b2e7f5",
        },
        {
            "repository": "https://github.com/unitreerobotics/unitree_mujoco",
            "commit": "ae6a8403e272733e9996ef59990880330496177f",
        },
    ]
    assert definition["model"]["path"] == "/opt/unitree_mujoco/unitree_robots/go2/scene.xml"
    assert definition["model"]["robot_model_path"] == "unitree_robots/go2/go2.xml"
    assert definition["route"]["cli"] == "extensions/sdk/scripts/run_unitree_go2_route_check.py"
    assert definition["route"]["module"] == "autoadapter2_sdk.unitree_go2.route_check:run_route_check"
    assert "general_demo" not in text
    assert "autoadapter2.integrations" not in text
    assert "autoadapter2_sdk" in text
    assert "run_unitree_go2_route_check.py" in text


def test_evidence_readme_states_so_limitation() -> None:
    text = (SDK_ROOT / "evidence" / "README.md").read_text(encoding="utf-8")

    assert "historical General Demo artifacts" in text
    assert "No current runtime consumes them" in text
    assert "per-check payloads" in text
    assert "incomplete" in text
    assert "DRAFT/PASS mismatch" in text
    assert "exact SDK" in text
