#!/usr/bin/env python3
"""为精确匹配的本机 Wine 构建 RichEdit 回调副本，不安装 DLL。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE = Path("/usr/lib/wine/i386-windows/riched20.dll")
EXPECTED = "18117302c2dbc043e0b35aea4f6e40120dd95ffe2eb5720a80f0d7f522eb0cc3"
FIXED = "bbd98cd94d20d6858f99c7f5d5228bd76317b86dc0879e3f8f09780f4c7dfb12"
BASE, HOOK, STUB = 0x7AC00000, 0x32CB5, 0x3E400


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_source(original):
    require(hashlib.sha256(original).hexdigest() == EXPECTED,
            "原始 DLL 哈希不匹配；拒绝给未知 Wine 构建应用补丁")
    checks = (
        (HOOK, "b80d000000"),
        (0x32CA6, "ff15dc94c67a83ec0489c285c0757b"),
        (0x28C7A, "8b83b4040000"),
        (0x2AEB8, "8988b4040000"),
    )
    for offset, hexadecimal in checks:
        expected = bytes.fromhex(hexadecimal)
        require(original[offset:offset + len(expected)] == expected,
                f"关键指令不匹配：{offset:#x}")
    require(original[0x178:0x180].rstrip(b"\0") == b".text",
            "可执行节名称不匹配")
    require(struct.unpack_from("<IIII", original, 0x180) ==
            (0x3D358, 0x1000, 0x3E000, 0x1000), "可执行节布局不匹配")


def apply_stub(original, stub):
    validate_source(original)
    require(len(stub) == 111, "回调机器码大小不匹配")
    require(set(original[STUB:STUB + len(stub)]) == {0},
            "预留空间不是全零，拒绝覆盖")
    patched = bytearray(original)
    patched[HOOK:HOOK + 5] = b"\xe9" + struct.pack("<i", STUB - (HOOK + 5))
    patched[STUB:STUB + len(stub)] = stub
    struct.pack_into("<I", patched, 0x180, STUB + len(stub) - 0x1000)
    require(hashlib.sha256(patched).hexdigest() == FIXED,
            "补丁结果哈希不匹配，拒绝输出")
    return patched


def build(source, output):
    original = source.read_bytes()
    validate_source(original)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assemble-", dir=output) as temp:
        temp = Path(temp)
        subprocess.run([
            "as", "--32", "-o", str(temp / "callback.o"),
            str(ROOT / "callback.s"),
        ], check=True)
        subprocess.run([
            "ld", "-m", "elf_i386", "-Ttext", hex(BASE + STUB),
            "-e", "callback_entry", "-o", str(temp / "callback.elf"),
            str(temp / "callback.o"),
        ], check=True, capture_output=True)
        subprocess.run([
            "objcopy", "-O", "binary", "-j", ".text",
            str(temp / "callback.elf"), str(temp / "callback.bin"),
        ], check=True)
        stub = (temp / "callback.bin").read_bytes()
    patched = apply_stub(original, stub)
    (output / "riched20.dll").write_bytes(patched)
    (output / "callback.bin").write_bytes(stub)
    manifest = {
        "状态": "已构建",
        "source_sha256": EXPECTED,
        "candidate_sha256": FIXED,
        "architecture": "i386 PE",
        "hook_rva": hex(HOOK),
        "stub_rva": hex(STUB),
        "stub_bytes": len(stub),
        "说明": "恢复 QueryAcceptData，复用原数据释放和默认粘贴路径",
        "不写剪贴板": True,
        "派生 DLL 许可证": "LGPL-2.1-or-later",
    }
    (output / "riched20-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("RichEdit 副本已生成，原始和输出哈希均通过")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()
