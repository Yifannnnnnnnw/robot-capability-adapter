from autoadapter2.environment import check_environment


def test_declared_environment_is_available() -> None:
    result = check_environment()

    assert result["mujoco"] == "3.9.0"
    assert result["numpy"] == "2.4.6"
    assert result["mujoco_physics_smoke"] is True
