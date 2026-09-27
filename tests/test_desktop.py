"""验证目录队列边界、URI 转换与显式默认程序修改的安全边界。"""
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


bridge = load("thunar_bridge", "modules/desktop/thunar-bridge.py")
uri = load("wecom_xdg", "modules/desktop/bin/xdg-open")


class DesktopTests(unittest.TestCase):
    def test_incomplete_record_is_retained(self):
        stream = io.BytesIO(struct.pack("<I", 8) + b"/tmp")
        self.assertIsNone(bridge.parse_record(stream))
        self.assertEqual(stream.tell(), 0)

    def test_invalid_size_is_rejected(self):
        for length in (0, bridge.MAX_RECORD + 1):
            with self.assertRaises(ValueError):
                bridge.parse_record(io.BytesIO(struct.pack("<I", length)))

    def test_chinese_space_directory_and_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "中文 空格"
            folder.mkdir()
            file = folder / "测试文件.txt"
            file.touch()
            for path in (folder, file):
                value = os.fsencode(path)
                record = struct.pack("<I", len(value)) + value
                self.assertEqual(bridge.parse_record(io.BytesIO(record)),
                                 folder)

    def test_relative_and_nul_paths_are_rejected(self):
        for payload in (b"relative/path", b"/tmp/\0invalid", b"\xff"):
            record = struct.pack("<I", len(payload)) + payload
            self.assertFalse(bridge.parse_record(io.BytesIO(record)))

    def test_queue_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.touch(mode=0o600)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                bridge.secure_open(link)

    def test_queue_permissions_and_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.touch(mode=0o644)
            with self.assertRaises(PermissionError):
                bridge.secure_open(target)
            target.chmod(0o600)
            os.link(target, root / "other")
            with self.assertRaises(PermissionError):
                bridge.secure_open(target)

    def test_wine_file_uri_is_normalized(self):
        self.assertEqual(uri.normalize(r"file://home\测试 空间\a#b"),
                         "file:///home/%E6%B5%8B%E8%AF%95%20"
                         "%E7%A9%BA%E9%97%B4/a%23b")

    def test_valid_uri_and_plain_path_are_unchanged(self):
        for value in ("https://example.com/a?q=b", "wemeet://test",
                      "file://server/share", "file:///tmp/test",
                      "/tmp/中文 file"):
            self.assertEqual(uri.normalize(value), value)


if __name__ == "__main__":
    unittest.main()
