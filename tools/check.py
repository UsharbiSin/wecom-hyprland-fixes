#!/usr/bin/env python3
"""检查文本规范与可离线运行的测试，不接触真实 Wine 前缀。"""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "build", ".local", "__pycache__"}
errors = []
for path in sorted(ROOT.rglob("*")):
    relative = path.relative_to(ROOT)
    if set(relative.parts) & SKIP or not path.is_file():
        continue
    if path.suffix == ".pyc":
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"禁止提交二进制文件：{relative}")
        continue
    for number, line in enumerate(text.splitlines(), 1):
        # 许可证法律原文不改写、不截断。
        if len(line) > 80 and relative.parts[0] != "licenses":
            errors.append(f"{relative}:{number} 超过 80 字符")
        if line.rstrip() != line and path.suffix != ".patch":
            errors.append(f"{relative}:{number} 行末空白")
        personal = "/home/" + "usharbisin|/run/user/" + "1000|gh[pousr]_"
        if (re.search(personal, line)
                and relative.as_posix() != "tools/check.py"):
            errors.append(f"{relative}:{number} 含私人路径或令牌标记")
    if text and not text.endswith("\n"):
        errors.append(f"{relative} 缺少行尾换行")
    if path.suffix == ".json":
        try:
            json.loads(text)
        except ValueError as error:
            errors.append(f"{relative}：{error}")
    if path.suffix == ".py" or relative.as_posix() == "wecom-fix":
        try:
            ast.parse(text)
        except SyntaxError as error:
            errors.append(f"{relative}：{error}")
    if path.suffix == ".sh":
        if subprocess.run(["bash", "-n", str(path)]).returncode:
            errors.append(f"{relative}：Shell 语法失败")
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=ROOT,
)
if result.returncode:
    raise SystemExit(result.returncode)
print("通过：80 字符、隐私路径、文本、JSON、Python、Shell 与单元测试。")
