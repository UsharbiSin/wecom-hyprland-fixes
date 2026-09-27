#!/usr/bin/env python3
"""确认编译结果只导入新增格式所需 API，没有清空或接管剪贴板 API。"""

from pathlib import Path
import re
import subprocess
import sys


def check(executable):
    result = subprocess.run([
        "x86_64-w64-mingw32-objdump", "-p", str(executable),
    ], check=True, capture_output=True, text=True)
    forbidden = (
        "EmptyClipboard", "OleSetClipboard", "OleFlushClipboard",
        "OleGetClipboard",
    )
    for name in forbidden:
        if re.search(r"\b" + name + r"\b", result.stdout):
            raise ValueError(f"发现禁止的剪贴板导入：{name}")
    if not re.search(r"\bSetClipboardData\b", result.stdout):
        raise ValueError("缺少预期的格式追加 API")
    print("输出辅助程序导入检查通过：无清空或接管剪贴板 API")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
