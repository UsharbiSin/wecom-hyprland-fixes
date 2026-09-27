#!/usr/bin/env python3
"""维护者显式同步 GitHub 仓库规则；需要对应仓库管理权限。"""
import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", default="UsharbiSin/wecom-hyprland-fixes")
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
commands = [
    ["gh", "api", f"repos/{args.repo}", "--method", "PATCH",
     "-F", "allow_squash_merge=false", "-F", "allow_rebase_merge=false",
     "-F", "allow_merge_commit=true", "-F", "delete_branch_on_merge=true"],
    ["gh", "api", f"repos/{args.repo}/branches/main/protection",
     "--method", "PUT", "--input",
     str(root / ".github/branch-protection.json")],
    ["gh", "api", f"repos/{args.repo}/branches/main/protection/"
     "required_signatures", "--method", "POST"],
]
for command in commands:
    print(" ".join(command), flush=True)
    if args.apply:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
if not args.apply:
    print("默认仅展示计划；增加 --apply 才更新远端规则。")
