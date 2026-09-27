"""摄像头源码准备过程的输入校验和文件隔离测试。"""

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "modules" / "camera" / "prepare.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CameraPreparationTests(unittest.TestCase):
    def test_unsupported_source_does_not_create_output(self):
        prepare = load(PREPARE, "camera_prepare")
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "wine"
            inputs = source / "dlls" / "qcap"
            inputs.mkdir(parents=True)
            for file in prepare.SOURCES:
                (inputs / file).write_text("不兼容源码\n")
            output = folder / "build"
            with self.assertRaises(ValueError):
                prepare.prepare(source, output)
            self.assertFalse(output.exists())
            for file in prepare.SOURCES:
                self.assertEqual(
                    (inputs / file).read_text(), "不兼容源码\n"
                )

    def test_output_inside_upstream_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "wine"
            source.mkdir()
            output = source / "generated"
            result = subprocess.run(
                [sys.executable, str(PREPARE), "--source", str(source),
                 "--output", str(output)], capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
