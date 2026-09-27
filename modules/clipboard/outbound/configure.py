#!/usr/bin/env python3
"""生成专用前缀配置。"""

import argparse
from pathlib import Path


def cpp_literal(value):
    """用通用字符名编码，避免路径中的引号变成 C++ 源码。"""
    chunks = []
    for character in value:
        code = ord(character)
        if character in ('"', "\\"):
            chunks.append("\\" + character)
        elif 32 <= code <= 126:
            chunks.append(character)
        elif code <= 0xFFFF:
            chunks.append(f"\\u{code:04x}")
        else:
            chunks.append(f"\\U{code:08x}")
    # 按转义后的字符单元换行，不切断转义序列。
    lines, current = [], ""
    for chunk in chunks:
        if len(current) + len(chunk) > 60:
            lines.append(f'L"{current}"')
            current = ""
        current += chunk
    lines.append(f'L"{current}"')
    return "\n".join(lines)


def render_prefix(prefix):
    path = Path(prefix).expanduser()
    if not path.is_absolute():
        raise ValueError("WECOM_PREFIX 必须是绝对路径")
    if any(ord(char) < 32 for char in prefix):
        raise ValueError("前缀路径不能包含控制字符")
    value = str(path.resolve())
    return (
        "// 由 configure.py 根据专用前缀生成。\n"
        "#pragma once\n"
        "static const wchar_t kPrefix[] =\n"
        + cpp_literal(value)
        + ";\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render_prefix(args.prefix), encoding="utf-8")


if __name__ == "__main__":
    main()
