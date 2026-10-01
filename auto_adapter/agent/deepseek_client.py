"""Use DeepSeek's official Anthropic-compatible API with the native SDK."""
import os
from pathlib import Path


def create_deepseek_client():
    from anthropic import Anthropic

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        path = Path(__file__).resolve().parents[2] / ".env.deepseek"
        if path.is_file():
            for line in path.read_text().splitlines():
                name, separator, value = line.partition("=")
                if separator and name.strip() == "DEEPSEEK_API_KEY":
                    key = value.strip()
                    break
    if not key:
        raise ValueError("missing DeepSeek API credential (DEEPSEEK_API_KEY)")
    return Anthropic(api_key=key, base_url="https://api.deepseek.com/anthropic",
                     timeout=300.0, max_retries=0)
