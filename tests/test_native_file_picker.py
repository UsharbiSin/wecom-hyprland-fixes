"""Check the GTK bridge's path boundary without launching Wine or GTK."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_file_picker_bridge", ROOT / "modules/native-file-picker/bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class NativeFilePickerPathsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "prefix"
        self.dosdevices = self.prefix / "dosdevices"
        self.dosdevices.mkdir(parents=True)
        self.drive_c = self.prefix / "drive_c"
        self.drive_c.mkdir()
        (self.dosdevices / "c:").symlink_to(
            self.drive_c, target_is_directory=True)
        (self.dosdevices / "z:").symlink_to("/", target_is_directory=True)
        self.folder = self.root / "中文 附件"
        self.folder.mkdir()

    def file(self, name, folder=None):
        path = (folder or self.folder) / name
        path.write_text("local test fixture", encoding="utf-8")
        return path

    def decode(self, paths):
        payload = bridge.selection_payload(paths, self.prefix)
        return bytes.fromhex(payload).decode("utf-16-le")

    def test_single_chinese_space_filename_round_trips_utf16(self):
        path = self.file("测试 附件 😀.txt")
        directory = "Z:" + str(self.folder).replace("/", "\\")
        self.assertEqual(self.decode([path]),
                         directory + "\0" + path.name + "\0\0")

    def test_same_directory_multiselect_keeps_order_and_basenames(self):
        first = self.file("第一份 合同.txt")
        second = self.file("second file.pdf")
        directory = "Z:" + str(self.folder).replace("/", "\\")
        self.assertEqual(self.decode([second, first]),
                         directory + "\0second file.pdf\0第一份 合同.txt\0\0")

    def test_multiselect_from_different_directories_is_rejected(self):
        other = self.root / "另一个目录"
        other.mkdir()
        paths = [self.file("first.txt"), self.file("second.txt", other)]
        with self.assertRaises(ValueError):
            bridge.selection_payload(paths, self.prefix)

    def test_missing_file_and_directory_are_rejected(self):
        for path in (self.folder / "missing.txt", self.folder):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    bridge.selection_payload([path], self.prefix)

    def test_windows_incompatible_filename_characters_are_rejected(self):
        for character in '\\"\n\r<>|?*:':
            with self.subTest(character=repr(character)):
                path = self.file("invalid" + character + "name.txt")
                with self.assertRaises(ValueError):
                    bridge.selection_payload([path], self.prefix)

    def test_trailing_dots_and_spaces_cannot_select_another_file(self):
        self.file("report.txt")
        for name in ("report.txt.", "report.txt ", "report.txt. "):
            with self.subTest(name=name):
                path = self.file(name)
                with self.assertRaises(ValueError):
                    bridge.selection_payload([path], self.prefix)

    def test_reserved_device_names_are_rejected_case_insensitively(self):
        for name in ("CON", "prn.txt", "Aux.pdf", "nul.log", "COM1.txt",
                     "com9", "LPT1", "lpt9.csv", "COM¹.txt", "LPT²"):
            with self.subTest(name=name):
                path = self.file(name)
                with self.assertRaises(ValueError):
                    bridge.selection_payload([path], self.prefix)

    def test_ambiguous_directory_components_are_rejected(self):
        for name in ("folder.", "folder ", "NUL", "com1.files"):
            with self.subTest(name=name):
                folder = self.folder / name / "ordinary child"
                folder.mkdir(parents=True)
                path = self.file("normal.txt", folder)
                with self.assertRaises(ValueError):
                    bridge.selection_payload([path], self.prefix)

    def test_non_device_prefixes_and_internal_spaces_remain_usable(self):
        for name in ("CONtract.txt", "AUXiliary.txt", "COM10.txt",
                     "LPT10.csv", "report .txt", ".hidden"):
            with self.subTest(name=name):
                path = self.file(name)
                self.assertEqual(self.decode([path]).split("\0")[1], name)

    def test_chooser_without_filters_sends_zero_to_guard(self):
        path = self.file("no-filter.txt")
        dialog = Mock()
        dialog.get_filenames.return_value = [str(path)]
        dialog.get_filter.return_value = None
        dialog.get_current_folder.return_value = None
        state = SimpleNamespace(
            Gtk=SimpleNamespace(ResponseType=SimpleNamespace(ACCEPT=1)),
            args=SimpleNamespace(prefix=self.prefix),
            request={"multi": False}, filters=[], send=Mock(),
            destroy_dialog=Mock())
        bridge.Bridge.response(state, dialog, 1)
        state.send.assert_called_once()
        action, suffix = state.send.call_args.args
        self.assertEqual(action, "select")
        self.assertEqual(suffix.split()[0], "0")
        state.destroy_dialog.assert_called_once_with(clear_request=False)

    def test_changed_z_drive_mapping_is_rejected(self):
        path = self.file("test.txt")
        (self.dosdevices / "z:").unlink()
        (self.dosdevices / "z:").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            bridge.selection_payload([path], self.prefix)

    def test_missing_z_drive_mapping_is_rejected(self):
        path = self.file("test.txt")
        (self.dosdevices / "z:").unlink()
        with self.assertRaises(ValueError):
            bridge.selection_payload([path], self.prefix)

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            bridge.selection_payload([], self.prefix)

    def test_known_drive_handles_chinese_components_and_uppercase(self):
        actual = bridge.windows_to_local(r"C:\资料 空格\附件.txt", self.prefix)
        self.assertEqual(actual.resolve(), self.drive_c / "资料 空格" / "附件.txt")
        self.assertEqual(bridge.windows_to_local("c:\\", self.prefix).resolve(),
                         self.drive_c)

    def test_z_drive_converts_absolute_unix_path(self):
        actual = bridge.windows_to_local(r"Z:\tmp\测试 文件.txt", self.prefix)
        self.assertEqual(actual.resolve(), Path("/tmp/测试 文件.txt"))

    def test_unknown_drive_is_rejected(self):
        self.assertIsNone(bridge.windows_to_local(r"Q:\missing", self.prefix))

    def test_relative_unc_and_non_windows_paths_are_rejected(self):
        for value in ("", "C:", "C:relative", "C:/wrong-slash", "/tmp/file",
                      r"\\server\share\file", "Ｃ:\\file"):
            with self.subTest(value=value):
                self.assertIsNone(bridge.windows_to_local(value, self.prefix))


if __name__ == "__main__":
    unittest.main()
