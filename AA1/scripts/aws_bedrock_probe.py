"""Probe whether the new sandbox AWS account can run Bedrock Claude.

Run AFTER setting credentials, e.g.:
    AWS_PROFILE=your-aws-profile python scripts/aws_bedrock_probe.py

Checks, per candidate region:
  1. STS identity (which account are we in)
  2. bedrock:ListFoundationModels (is Bedrock reachable / not SCP-blocked)
  3. whether any Anthropic Claude models are listed (model access enabled)
  4. a tiny live Converse call on the region's inference profile

Prints a verdict: which region+model-id prefix to use. Exposes NO secrets.
"""
import os

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# region -> inference-profile prefix our code would use there
CANDIDATES = {
    "us-east-1": "us",
    "us-west-2": "us",
    "eu-west-2": "eu",
    "eu-central-1": "eu",
    "eu-west-1": "eu",
    "eu-west-3": "eu",
}
# a cheap model to smoke-test Converse with, per prefix
SMOKE = {
    "us": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "eu": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
}

sess = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"))
try:
    ident = sess.client("sts", region_name="us-east-1").get_caller_identity()
    print(f"[identity] account={ident['Account']}  arn={ident['Arn']}")
except (ClientError, BotoCoreError) as e:
    print(f"[identity] FAILED: {e}")
    raise SystemExit("Credentials not usable — set AWS_PROFILE or env vars first.")

print()
verdict = []
for region, prefix in CANDIDATES.items():
    print(f"=== {region} (prefix '{prefix}.') ===")
    # 1. ListFoundationModels
    try:
        br = sess.client("bedrock", region_name=region)
        models = br.list_foundation_models().get("modelSummaries", [])
        claude = [m["modelId"] for m in models
                  if "anthropic" in m["modelId"].lower()]
        print(f"  list-models: OK ({len(models)} total, {len(claude)} Anthropic)")
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        print(f"  list-models: BLOCKED ({code})")
        continue
    # 2. live Converse smoke test
    smoke_id = SMOKE[prefix]
    try:
        rt = sess.client("bedrock-runtime", region_name=region)
        rt.converse(
            modelId=smoke_id,
            messages=[{"role": "user", "content": [{"text": "say OK"}]}],
            inferenceConfig={"maxTokens": 5},
        )
        print(f"  converse {smoke_id}: OK  <-- USABLE")
        verdict.append((region, prefix, smoke_id))
    except (ClientError, BotoCoreError) as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
        print(f"  converse {smoke_id}: FAIL ({code})")
    print()

print("=" * 60)
if verdict:
    region, prefix, mid = verdict[0]
    print(f"VERDICT: use region={region}, model-id prefix='{prefix}.'")
    if prefix == "us":
        print("  -> us-east-1 works: credentials swap only, NO code changes.")
    else:
        print("  -> EU only: switch model IDs us.anthropic.* -> eu.anthropic.*")
        print(f"     and aws_region -> {region}.")
else:
    print("VERDICT: NO usable Bedrock Claude region found in this account.")
    print("  -> Bedrock model access likely not enabled, or SCP-blocked.")
    print("     Ask your AWS admin to (a) enable Anthropic Claude model access in")
    print("     Bedrock for this account, and (b) confirm no SCP blocks bedrock:*.")
