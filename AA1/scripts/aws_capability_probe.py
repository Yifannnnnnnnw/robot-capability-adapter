"""Map what the Sandbox-6 account can actually do (given Bedrock InvokeModel
is SCP-denied). All calls are READ-ONLY list/describe, plus one EC2 RunInstances
DryRun (creates nothing). No resources are provisioned, no cost incurred.

Run with env-scoped temp creds:
    python scripts/aws_capability_probe.py
"""
import boto3
from botocore.exceptions import ClientError, BotoCoreError

REGION = "us-east-1"


def _try(label, fn):
    try:
        fn()
        print(f"  [OK]      {label}")
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # AccessDenied / explicit-deny = blocked; everything else = service
        # reachable (call-shape issue), so treat as ALLOWED for perms purposes.
        scp = "explicit deny in a service control policy" in e.response["Error"]["Message"]
        tag = "DENIED(SCP)" if scp else ("DENIED" if "AccessDenied" in code else f"ok? ({code})")
        print(f"  [{tag}] {label}")
        return tag.startswith("ok")
    except BotoCoreError as e:
        print(f"  [err]     {label}: {type(e).__name__}")
        return False


s = boto3.Session()
ident = s.client("sts", region_name=REGION).get_caller_identity()
print(f"account={ident['Account']}  region={REGION}\n")

c = lambda name: s.client(name, region_name=REGION)

print("== Compute / hosting (could self-host an LLM here) ==")
_try("ec2:DescribeInstances", lambda: c("ec2").describe_instances())
_try("ec2:RunInstances (DryRun, no launch)",
     lambda: c("ec2").run_instances(ImageId="ami-00000000000000000",
                                    MinCount=1, MaxCount=1,
                                    InstanceType="t3.micro", DryRun=True))
_try("sagemaker:ListDomains", lambda: c("sagemaker").list_domains())
_try("sagemaker:ListNotebookInstances", lambda: c("sagemaker").list_notebook_instances())
_try("sagemaker:ListEndpoints", lambda: c("sagemaker").list_endpoints())
_try("sagemaker:ListModels", lambda: c("sagemaker").list_models())

print("\n== Storage / data ==")
_try("s3:ListBuckets", lambda: c("s3").list_buckets())
_try("dynamodb:ListTables", lambda: c("dynamodb").list_tables())
_try("ecr:DescribeRepositories", lambda: c("ecr").describe_repositories())

print("\n== Serverless / orchestration (agent system glue) ==")
_try("lambda:ListFunctions", lambda: c("lambda").list_functions())
_try("states:ListStateMachines", lambda: c("stepfunctions").list_state_machines())
_try("events:ListRules", lambda: c("events").list_rules())
_try("apigateway-v2:GetApis", lambda: c("apigatewayv2").get_apis())

print("\n== AI services (besides Bedrock invoke) ==")
_try("bedrock:ListFoundationModels", lambda: c("bedrock").list_foundation_models())
_try("bedrock-agent:ListAgents", lambda: c("bedrock-agent").list_agents())
_try("bedrock-runtime:Converse (expect SCP deny)",
     lambda: c("bedrock-runtime").converse(
         modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
         messages=[{"role": "user", "content": [{"text": "hi"}]}],
         inferenceConfig={"maxTokens": 5}))
_try("q (amazon q) / comprehend:ListEndpoints",
     lambda: c("comprehend").list_endpoints())

print("\n== Identity / config (read) ==")
_try("iam:ListRoles", lambda: c("iam").list_roles(MaxItems=1))
_try("logs:DescribeLogGroups", lambda: c("logs").describe_log_groups(limit=1))
_try("secretsmanager:ListSecrets", lambda: c("secretsmanager").list_secrets(MaxResults=1))
_try("cloudformation:ListStacks", lambda: c("cloudformation").list_stacks())

print("\n== Region scope: is the block bedrock-only or account-wide? ==")
for r in ["us-east-1", "us-east-2", "us-west-2", "eu-west-2",
          "eu-west-1", "eu-central-1", "eu-west-3"]:
    # ec2 describe = a non-bedrock canary for whether the REGION is allowed
    try:
        s.client("ec2", region_name=r).describe_availability_zones()
        ec2ok = "ec2:OK"
    except ClientError as e:
        msg = e.response["Error"]["Message"]
        ec2ok = "ec2:SCP-DENY" if "service control policy" in msg else f"ec2:{e.response['Error']['Code']}"
    except BotoCoreError:
        ec2ok = "ec2:err"
    print(f"  {r:14s} {ec2ok}")
