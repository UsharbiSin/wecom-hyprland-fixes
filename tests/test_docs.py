"""文档模块的版本拒绝、文件隔离和机器码分支测试。"""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "modules" / "docs" / "patch-libcef.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DocumentPatchTests(unittest.TestCase):
    def setUp(self):
        self.patch = load(PATCHER, "document_patch")

    def test_unknown_binary_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "source.dll"
            target = folder / "output.dll"
            original = b"unsupported-vendor-file"
            source.write_bytes(original)
            result = subprocess.run(
                [sys.executable, str(PATCHER), "--source", str(source),
                 "--output", str(target)], capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_hard_link_and_symbolic_link_do_not_replace_source(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "source.dll"
            source.write_bytes(b"original")
            for kind in ("hard", "symbolic"):
                target = folder / f"{kind}.dll"
                if kind == "hard":
                    target.hardlink_to(source)
                else:
                    target.symlink_to(source)
                result = subprocess.run(
                    [sys.executable, str(PATCHER), "--source", str(source),
                     "--output", str(target)], capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(source.read_bytes(), b"original")

    def test_machine_code_branches_have_exact_destinations(self):
        pairs = [
            (b"\x0f\x85", self.patch.BRANCH_RVA, self.patch.CAVE_RVA),
            (b"\x0f\x85", self.patch.CAVE_RVA + 7, self.patch.TRAP_RVA),
            (b"\xe9", self.patch.CAVE_RVA + 13, self.patch.RESUME_RVA),
        ]
        for opcode, origin, destination in pairs:
            instruction = self.patch.rel32(opcode, origin, destination)
            self.assertEqual(
                self.patch.target(instruction, origin, len(opcode)),
                destination,
            )


if __name__ == "__main__":
    unittest.main()
