"""Upload AutoAdapter-Bench BULK artifacts to a HuggingFace dataset repo.

The lean code+paper+result-JSONs live on GitHub; the heavy media/traces (too
big for GitHub) go here. Run after `huggingface-cli login` (needs a write token).

  python scripts/upload_hf.py --repo-id <user-or-org>/autoadapter-bench

What gets uploaded (the bulk GitHub's .gitignore excludes):
  - autoadapter_bench/results/**            (result JSONs + showcase videos + per-task videos)
  - artifacts/**/recordings/                (per-task replay videos, ~792 MB)
  - artifacts/**/traces/, **/task_traces/   (agent ReAct trace jsonl)
  - assets/mjcf/                            (MJCF scenes/meshes, ~306 MB — self-contained reproduction)
Driver source (.py) and the paper stay on GitHub; this dataset is the
reproduction/visualization bundle that GitHub points to.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = [
    "autoadapter_bench/results",
    "artifacts",        # filtered by allow_patterns below to media+traces only
    "assets/mjcf",
]
# Only ship media + traces from artifacts (not pycache); keep it a clean bundle.
ALLOW = ["*.mp4", "*.jsonl", "*.json", "*.xml", "*.png", "*.stl", "*.obj",
         "*.md", "*.yaml", "*.txt", "driver*.py", "**/driver*.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-id", required=True, help="e.g. yourname/autoadapter-bench")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from huggingface_hub import HfApi, create_repo  # noqa: PLC0415
    api = HfApi()
    create_repo(args.repo_id, repo_type="dataset", private=args.private, exist_ok=True)
    # dataset card
    card = ROOT / "docs" / "HF_DATASET_README.md"  # upload last would be safer; results/README.md also maps to root README
    if card.exists():
        api.upload_file(path_or_fileobj=str(card), path_in_repo="README.md",
                        repo_id=args.repo_id, repo_type="dataset")
    for sub in INCLUDE:
        src = ROOT / sub
        if not src.exists():
            print(f"  skip (missing): {sub}"); continue
        print(f"  uploading {sub} ...")
        if args.dry_run:
            continue
        api.upload_large_folder(
            folder_path=str(src), repo_id=args.repo_id, repo_type="dataset",
            allow_patterns=ALLOW,
        )
    print("DONE. Dataset:", f"https://huggingface.co/datasets/{args.repo_id}")


if __name__ == "__main__":
    main()
