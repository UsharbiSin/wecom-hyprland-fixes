#!/usr/bin/env python3
"""只从校验过的 Wine 11.17 源码生成独立 qcap 构建输入。"""

import argparse
import hashlib
from pathlib import Path

SOURCES = {
    "v4l.c":
        "1c9783660d5d29eaa7f92feabbd6deaccd8d153912e135e91a3903d93478d56a",
    "qcap_private.h":
        "e6335541ca0b28e3b13a4c7175e96cf7cbc2ee11b08a4e0fbcf10e6ee193b6b0",
}


def prepare(source, output):
    contents = {}
    for name, expected in SOURCES.items():
        data = (source / "dlls" / "qcap" / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"{name} 不是支持的 Wine 11.17 原始源码")
        contents[name] = data.decode()
    code = contents["v4l.c"]
    marker = "    mt32->formattype           = mt->formattype;"
    if code.count(marker) != 1:
        raise ValueError("无法唯一定位媒体格式转换")
    # 在格式返回前初始化 COM 指针，修复 32 位调用方释放格式时的崩溃。
    initialization = "\n    mt32->pUnk                 = 0;"
    code = code.replace(marker, marker + initialization)
    header = contents["qcap_private.h"].replace(
        '#include "wine/strmbase.h"',
        "/* 独立 Unix 后端不使用 PE 专用的 strmbase 定义。 */",
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "v4l.c").write_text(code)
    (output / "qcap_private.h").write_text(header)
    (output / "config.h").write_text(
        "#define HAVE_ASM_TYPES_H 1\n"
        "#define HAVE_LINUX_VIDEODEV2_H 1\n"
        '#define SONAME_LIBV4L2 "libv4l2.so.0"\n'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error("构建目录不得位于上游源码目录内")
    try:
        prepare(source, output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"准备失败：{error}\n")
    print("Wine 11.17 源码校验完成，已在构建目录生成补丁。")


if __name__ == "__main__":
    main()
