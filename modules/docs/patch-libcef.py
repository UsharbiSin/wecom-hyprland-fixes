#!/usr/bin/env python3
"""向指定版本的私有 DLL 副本移植 V8 页面保护兼容判断。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

SOURCE_SHA256 = (
    "007385e85fce0e70788dbc362792d0738f6a7f5a045858924ab4789fa58a369f"
)
PATCHED_SHA256 = (
    "8f896cc2625a1c25bb8bbeaac3a4336296382eec82fdbf982485103fb9abcf79"
)
UPSTREAM = "df1eaa2bd84bf9cc8ff2d6b5e7ca53290546e4ab"
CHECK_RVA = 0x016A55E4
BRANCH_RVA = 0x016A55EB
RESUME_RVA = 0x016A55F1
TRAP_RVA = 0x016A59AC
CAVE_RVA = 0x01C62863
ORIGINAL_CHECK = bytes.fromhex("83bd2cffffff040f85bb030000")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rva_to_offset(data, rva):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    require(data[pe:pe + 4] == b"PE\0\0", "文件不是 PE 格式")
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    require(machine == 0x14C, "仅支持 32 位 x86 DLL")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    for index in range(count):
        entry = pe + 24 + optional_size + index * 40
        size, va, raw_size, raw = struct.unpack_from("<4I", data, entry + 8)
        if va <= rva < va + min(size, raw_size):
            return raw + rva - va
    raise ValueError(f"RVA 未映射到文件：{rva:#x}")


def rel32(opcode, origin, destination):
    return opcode + struct.pack("<i", destination - origin - len(opcode) - 4)


def target(insn, origin, opcode_size):
    offset = struct.unpack_from("<i", insn, opcode_size)[0]
    return origin + opcode_size + 4 + offset


def patch(original):
    require(sha256(original) == SOURCE_SHA256, "DLL 哈希不匹配，拒绝生成补丁")
    check = rva_to_offset(original, CHECK_RVA)
    branch_pos = rva_to_offset(original, BRANCH_RVA)
    cave_pos = rva_to_offset(original, CAVE_RVA)
    trap = rva_to_offset(original, TRAP_RVA)
    require(original[check:check + 13] == ORIGINAL_CHECK, "保护判断字节不匹配")
    require(original[trap:trap + 3] == b"\xcc\x0f\x0b", "失败陷阱字节不匹配")
    padding = b"\xc3" + b"\xcc" * 29
    require(original[cave_pos - 1:cave_pos + 29] == padding, "代码空隙不匹配")

    # 保留对保护值 4 的原比较，只将失败分支引向对值 8 的第二次比较。
    branch = rel32(b"\x0f\x85", BRANCH_RVA, CAVE_RVA)
    cave = bytes.fromhex("83bd2cffffff08")
    cave += rel32(b"\x0f\x85", CAVE_RVA + 7, TRAP_RVA)
    cave += rel32(b"\xe9", CAVE_RVA + 13, RESUME_RVA)
    require(target(branch, BRANCH_RVA, 2) == CAVE_RVA, "入口分支错误")
    failure = target(cave[7:13], CAVE_RVA + 7, 2)
    success = target(cave[13:18], CAVE_RVA + 13, 1)
    require(failure == TRAP_RVA and success == RESUME_RVA, "出口分支错误")
    require(original[check:check + 6] == cave[:6], "比较地址不一致")
    require(original[check + 6] == 4 and cave[6] == 8, "保护值不匹配")

    patched = bytearray(original)
    patched[branch_pos:branch_pos + len(branch)] = branch
    patched[cave_pos:cave_pos + len(cave)] = cave
    # 最终哈希同时约束文件长度、修改范围、PE 头和所有未改代码。
    require(sha256(patched) == PATCHED_SHA256, "生成结果哈希不匹配")
    manifest = {
        "upstream_commit": UPSTREAM,
        "source_sha256": SOURCE_SHA256,
        "patched_sha256": PATCHED_SHA256,
        "file_size": len(original),
        "accepted_old_protection": [4, 8],
        "virtualprotect_readonly_preserved": True,
    }
    return patched, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="原版 DLL")
    parser.add_argument("--output", type=Path, required=True, help="私有副本路径")
    args = parser.parse_args()
    same = args.source.resolve() == args.output.resolve()
    if args.output.exists():
        same = same or args.source.samefile(args.output)
    if same:
        parser.error("禁止覆盖原版 DLL，包括符号链接或硬链接")
    try:
        patched, manifest = patch(args.source.read_bytes())
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f"构建失败：{error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    args.output.with_suffix(".json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print("文档私有副本已生成，SHA-256 校验通过。")


if __name__ == "__main__":
    main()
