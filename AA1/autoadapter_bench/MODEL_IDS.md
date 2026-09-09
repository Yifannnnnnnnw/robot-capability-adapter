# Model ID Mapping & Pricing

Short names used in result filenames (`{method}_{robot}_{short}.json`) → exact
Bedrock model identifiers and per-token pricing (USD per 1M tokens).

| Short | Vendor | Bedrock Model ID | $/M in | $/M out | Tool-use |
|---|---|---|---:|---:|---|
| haiku45 | Anthropic | us.anthropic.claude-haiku-4-5-20251001-v1:0 | 1.00 | 5.00 | Anthropic API |
| sonnet46 | Anthropic | us.anthropic.claude-sonnet-4-6 | 3.00 | 15.00 | Anthropic API |
| opus48 | Anthropic | us.anthropic.claude-opus-4-8 | 5.00 | 25.00 | Anthropic API |
| ministral8b | Mistral AI | mistral.ministral-3-8b-instruct | 0.10 | 0.10 | Bedrock Converse |
| qwen32 | Alibaba | qwen.qwen3-32b-v1:0 | 0.15 | 0.60 | Bedrock Converse |
| deepseek | DeepSeek | deepseek.v3.2 | 0.27 | 1.10 | Bedrock Converse |
| novapro | Amazon | amazon.nova-pro-v1:0 | 0.80 | 3.20 | Bedrock Converse |

## Excluded models (smoketest failed)

| Model | Bedrock ID | Reason |
|---|---|---|
| Gemma 3 27B | google.gemma-3-27b-it | No Converse tool-use support (replies text, never calls tool) |
| Llama 3.3/4 | meta.llama3-3-70b-instruct-v1:0 | ValidationException: direct model ID not invokable (needs inference profile) |
| GPT-5.5 | (OpenAI) | Not on Bedrock; no OpenAI API key configured |

## Notes
- Anthropic models use `anthropic.AnthropicBedrock` client (native tool-use).
- Non-Anthropic models use `boto3 bedrock-runtime converse` via the
  `auto_adapter/agent/converse_client.py` shim (uniform tool-use).
- `opus-4-8` deprecates the `temperature` kwarg; eval strips it for opus-4-8/4-9.
- Pricing as of 2026-06; verify against current Bedrock pricing before final cost claims.
