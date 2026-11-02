#!/usr/bin/env python3
"""只读核对 Wine 输入桥依赖的上游源码，不自动授权未知二进制。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE_FILES = {
    "11.19": "abi-evidence.json",
    "11.20": "abi-evidence.json",
    "11.21": "abi-evidence-wine21.json",
}


def source_evidence(version):
    if version not in EVIDENCE_FILES:
        raise ValueError(f"没有 Wine {version} 的已审查源码证据")
    return json.loads((HERE / EVIDENCE_FILES[version]).read_text())


def audit(source, evidence=None, version="11.20"):
    source = Path(source).resolve()
    if evidence is None:
        evidence = source_evidence(version)
    results = {}
    for relative, expected in evidence["source_sha256"].items():
        path = (source / relative).resolve()
        if not path.is_relative_to(source):
            raise ValueError(f"源码路径超出目录：{relative}")
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            actual = None
        results[relative] = actual == expected
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Wine 上游源码根目录；必须与待核验包版本对应")
    parser.add_argument("--wine-version", choices=sorted(EVIDENCE_FILES),
                        default="11.20", help="选用对应版本的独立源码证据")
    args = parser.parse_args()
    results = audit(args.source, version=args.wine_version)
    for name, matches in results.items():
        print(f"{'相同' if matches else '需审查'} {name}")
    print("源码相同仅用于缩小接口审查范围，不能替代安装包核验与界面验证。")
    print("此命令不会修改清单、安装文件或启动任何 Wine 进程。")
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
