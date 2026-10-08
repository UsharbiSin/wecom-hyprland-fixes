#!/usr/bin/env python3
"""检查 PR 的命名规范；签名由 GitHub 原生规则管理。"""
import json
from pathlib import Path
import re
import subprocess
import sys

TYPES = "feat|fix|docs|refactor|test|build|ci|chore|perf|revert"
TITLE = re.compile(rf"^({TYPES})(\([a-z0-9-]+\))?!?: .+")
BRANCH = re.compile(rf"({TYPES})/[a-z0-9-]+")


def validate(title, branch, messages):
    if not TITLE.fullmatch(title) or len(title) > 80:
        raise ValueError("PR 标题必须符合 Conventional Commits 且不超过 80 字符")
    if not BRANCH.fullmatch(branch):
        raise ValueError("分支名必须采用 type/lowercase-name 格式")
    if not messages:
        raise ValueError("PR 没有待合并提交")
    for message in messages:
        lines = message.splitlines()
        if not lines or not TITLE.fullmatch(lines[0]):
            raise ValueError("提交标题必须符合 Conventional Commits")
        if any(len(line) > 80 for line in lines):
            raise ValueError("提交消息每行不得超过 80 字符")


def check_event(event):
    pull = event["pull_request"]
    base, head = pull["base"]["sha"], pull["head"]["sha"]
    for sha in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("无效的提交 SHA")
    commits = subprocess.check_output(
        ["git", "rev-list", f"{base}..{head}"], text=True,
    ).splitlines()
    messages = [subprocess.check_output(
        ["git", "show", "-s", "--format=%B", sha], text=True,
    ) for sha in commits]
    validate(pull["title"], pull["head"]["ref"], messages)


if __name__ == "__main__":
    check_event(json.loads(Path(sys.argv[1]).read_text()))
    print("通过：分支名、提交消息和 PR 标题。")
