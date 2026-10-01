"""Regression for the observed truncated driver.py tool arguments."""
import pytest

from auto_adapter.agent.tools import make_write_file_tool


def test_missing_content_keeps_file_and_guides_chunked_retry(tmp_path):
    tool = make_write_file_tool(tmp_path)
    tool.handler({"path": "driver.py", "content": "class Robot:\n"})
    with pytest.raises(ValueError, match="append=true"):
        tool.handler({"path": "driver.py"})
    tool.handler({"path": "driver.py", "content": "    pass\n", "append": True})
    assert (tmp_path / "driver.py").read_text() == "class Robot:\n    pass\n"
