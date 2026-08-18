from pathlib import Path

import pytest

from autoadapter2.self_containment import SelfContainmentError, check_self_contained


ROOT = Path(__file__).resolve().parents[1]


def test_demo3_is_self_contained() -> None:
    result = check_self_contained(ROOT)

    assert result["python_files_checked"] >= 1


def test_external_project_import_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("import demo2.pipeline\n", encoding="utf-8")

    with pytest.raises(SelfContainmentError, match="external project import"):
        check_self_contained(tmp_path)
