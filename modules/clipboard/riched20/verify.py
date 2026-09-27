#!/usr/bin/env python3
"""在两个加载地址执行完整粘贴机器码，不访问 Wine、界面或剪贴板。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
FIXED = "bbd98cd94d20d6858f99c7f5d5228bd76317b86dc0879e3f8f09780f4c7dfb12"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def map_pe(data, new_base):
    # 在执行任何 DLL 字节之前，校验整个文件。
    require(hashlib.sha256(data).hexdigest() == FIXED, "待测 DLL 哈希不匹配")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 24
    base = struct.unpack_from("<I", data, opt + 28)[0]
    size, headers = struct.unpack_from("<II", data, opt + 56)
    image = bytearray(size)
    image[:headers] = data[:headers]
    section = opt + struct.unpack_from("<H", data, pe + 20)[0]
    for number in range(struct.unpack_from("<H", data, pe + 6)[0]):
        _, rva, rawsize, raw = struct.unpack_from(
            "<IIII", data, section + number * 40 + 8)
        image[rva:rva + rawsize] = data[raw:raw + rawsize]
    reloc, length = struct.unpack_from("<II", data, opt + 96 + 5 * 8)
    position = reloc
    while position < reloc + length:
        page, size = struct.unpack_from("<II", image, position)
        require(size >= 8, "重定位块大小不合法")
        for offset in range(position + 8, position + size, 2):
            entry = struct.unpack_from("<H", image, offset)[0]
            kind, rva = entry >> 12, page + (entry & 4095)
            require(kind in (0, 3), "出现不支持的重定位种类")
            if kind == 3:
                require(not 0x3E400 <= rva < 0x3E46F,
                        "新增回调不应存在绝对地址重定位")
                value = struct.unpack_from("<I", image, rva)[0]
                struct.pack_into("<I", image, rva,
                                 (value + new_base - base) & 0xFFFFFFFF)
        position += size
    return image


def verify(build_dir):
    data = (build_dir / "riched20.dll").read_bytes()
    results = []
    for base in (0x7AC00000, 0x6AC00000):
        with tempfile.TemporaryDirectory(prefix="abi-", dir=build_dir) as tmp:
            folder = Path(tmp)
            (folder / "image.bin").write_bytes(map_pe(data, base))
            # 汇编从相对路径读取，构建目录即使含引号也不进入汇编源码。
            (folder / "start.s").write_text(
                '.global _start\n.section .text\n_start:\n'
                '    call test_main\n    movl %eax, %ebx\n'
                '    movl $1, %eax\n    int $0x80\n'
                '.section .wine,"awx"\n.incbin "image.bin"\n',
                encoding="utf-8",
            )
            (folder / "test.ld").write_text(
                'ENTRY(_start)\nPHDRS { text PT_LOAD FLAGS(5); '
                'data PT_LOAD FLAGS(6); wine PT_LOAD FLAGS(7); }\n'
                'SECTIONS { . = 0x08048000; .text : '
                '{ *(.text*) *(.rodata*) } :text\n'
                '. = ALIGN(0x1000); .data : '
                '{ *(.data*) *(.bss*) *(COMMON) } :data\n'
                f'. = {base:#x}; .wine : {{ *(.wine) }} :wine\n'
                '/DISCARD/ : { *(.eh_frame*) *(.note*) *(.comment*) } }\n',
                encoding="utf-8",
            )
            subprocess.run([
                "gcc", "-m32", "-ffreestanding", "-fno-pie",
                "-fno-stack-protector", "-fno-asynchronous-unwind-tables",
                "-O2", "-Wall", "-Wextra", "-Werror", f"-DLOAD_BASE={base:#x}",
                "-c", str(ROOT / "verify-harness.c"),
                "-o", str(folder / "test.o"),
            ], check=True)
            subprocess.run([
                "as", "--32", "-o", "start.o", "start.s",
            ], check=True, cwd=folder)
            subprocess.run([
                "ld", "-m", "elf_i386", "--no-warn-rwx-segments",
                "-T", "test.ld", "-o", "test", "start.o", "test.o",
            ], check=True, capture_output=True, cwd=folder)
            run = subprocess.run([str(folder / "test")],
                                 capture_output=True, timeout=15)
            if run.returncode:
                details = (struct.unpack("<6I", run.stdout)
                           if len(run.stdout) == 24 else run.stdout.hex())
                raise RuntimeError((hex(base), run.returncode, details))
            results.append({"base": hex(base), "cases": 14, "result": "PASS"})
    report = {
        "说明": "模拟 COM，直接执行完整 i386 粘贴函数机器码",
        "candidate_sha256": FIXED,
        "results": results,
        "覆盖": [
            "无回调原行为", "S_OK 继续默认或回调选择的格式",
            "CF_TEXT 正规化", "其他成功码短路", "失败码拒绝",
            "查询与实际粘贴标志", "数据对象恰好释放一次",
            "stdcall 栈平衡", "易失寄存器破坏", "PE 重定位",
        ],
        "clipboard_and_UI_used": False,
        "限制": "不能替代真实 Wine 加载和企业微信界面测试",
    }
    (build_dir / "riched20-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("RichEdit 完整机器码验证通过：两个地址，各 14 项")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    verify(parser.parse_args().build_dir.resolve())


if __name__ == "__main__":
    main()
