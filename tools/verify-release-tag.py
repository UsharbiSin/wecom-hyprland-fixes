#!/usr/bin/env python3
"""发布前核对 GitHub 已验证的注释标签及其目标，不运行本地 GPG。"""

import argparse
import json
import re
import subprocess

VERSION = r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
SHA = r"[0-9a-f]{40}"


def api_json(endpoint):
    result = subprocess.run(
        ["gh", "api", endpoint], check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def verify_tag(repo, tag, commit, api=api_json):
    if not re.fullmatch(VERSION, tag):
        raise ValueError("标签须为规范的 vMAJOR.MINOR.PATCH")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("仓库须为 owner/repo 格式")
    if not re.fullmatch(SHA, commit):
        raise ValueError("当前检出提交 SHA 无效")
    reference = api(f"repos/{repo}/git/ref/tags/{tag}")
    if reference.get("ref") != f"refs/tags/{tag}":
        raise ValueError("远端标签引用名称不一致")
    target = reference.get("object", {})
    if target.get("type") != "tag":
        raise ValueError("发布必须使用签名注释标签，不能使用轻量标签")
    tag_sha = target.get("sha", "")
    if not isinstance(tag_sha, str) or not re.fullmatch(SHA, tag_sha):
        raise ValueError("远端标签对象 SHA 无效")
    annotated = api(f"repos/{repo}/git/tags/{tag_sha}")
    if annotated.get("sha") != tag_sha or annotated.get("tag") != tag:
        raise ValueError("远端注释标签对象或名称不一致")
    verification = annotated.get("verification", {})
    if (verification.get("verified") is not True
            or verification.get("reason") != "valid"):
        raise ValueError("GitHub 尚未验证此标签签名；拒绝发布")
    target = annotated.get("object", {})
    if target.get("type") != "commit" or target.get("sha") != commit:
        raise ValueError("标签必须直接指向当前检出的提交")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip()
        verify_tag(args.repo, args.tag, commit)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print("通过：GitHub 已验证的注释标签直接指向当前检出提交。")


if __name__ == "__main__":
    main()
