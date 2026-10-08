#!/usr/bin/env python3
"""提取标签对应的变更记录；不访问网络或创建标签。"""

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def release_notes(changelog, tag):
    if not re.fullmatch(VERSION, tag):
        raise ValueError("标签须为 vMAJOR.MINOR.PATCH，不接受前导零或预发布")
    headings = list(re.finditer(r"^## .+$", changelog, re.MULTILINE))
    matches = [index for index, match in enumerate(headings)
               if re.fullmatch(rf"## {re.escape(tag)}(?: .*)?",
                               match.group())]
    if len(matches) != 1:
        raise ValueError(f"CHANGELOG 必须有且仅有一个 {tag} 章节")
    index = matches[0]
    start = headings[index].end()
    end = (headings[index + 1].start()
           if index + 1 < len(headings) else len(changelog))
    body = changelog[start:end].strip()
    if not body:
        raise ValueError(f"{tag} 的变更说明为空")
    return body + "\n\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--changelog", type=Path,
                        default=ROOT / "CHANGELOG.md")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        notes = release_notes(args.changelog.read_text(encoding="utf-8"),
                              args.tag)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    args.output.write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
