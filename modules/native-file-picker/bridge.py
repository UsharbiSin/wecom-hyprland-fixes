#!/usr/bin/env python3
"""Native GTK chooser for verified WeCom classic open dialogs."""
import argparse
import fcntl
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


COMDLG_HASHES = {
    "i386-windows":
        "406b934a9bab71f7a8cfb27d6cea701d8a79da297fbfa1d87af54b8466293b23",
    "x86_64-windows":
        "11ac4180f6332044daf116a5e3e80e4c9947d46f5ab83d500bd86f17520d7f54",
}


def windows_to_local(value, prefix):
    if len(value) < 3 or value[1:3] != ":\\" or not value[0].isascii():
        return None
    drive = prefix / "dosdevices" / (value[0].lower() + ":")
    if not drive.is_dir():
        return None
    return drive / value[3:].replace("\\", "/")


def selection_payload(paths, prefix):
    """Only existing files in one folder can round-trip through OPENFILENAME."""
    if not paths or len(paths) > 1024:
        raise ValueError("请选择文件。")
    values = [Path(path).absolute() for path in paths]
    if any(not p.is_file() for p in values):
        raise ValueError("所选文件已移动或无法读取，请重新选择。")
    if len({p.parent for p in values}) != 1:
        raise ValueError("企业微信一次多选需要来自同一个文件夹，请分次添加。")
    # The installed WeCom prefix maps Z: to /. Refuse a changed mapping.
    if (prefix / "dosdevices/z:").resolve() != Path("/"):
        raise ValueError("企业微信的文件路径映射已改变，请使用原窗口。")
    if any(any(c in str(p) for c in '\\"\0\n\r<>|?*:') for p in values):
        raise ValueError("文件名包含 Windows 不支持的字符，请使用原窗口。")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved |= {stem + digit for stem in ("COM", "LPT")
                 for digit in "123456789¹²³"}
    for path in values:
        if any(part.endswith((".", " ")) or
               part.split(".", 1)[0].upper() in reserved
               for part in path.parts[1:]):
            raise ValueError("这个文件名在 Windows 中有歧义，请使用原窗口。")
    directory = "Z:" + str(values[0].parent).replace("/", "\\")
    names = [p.name for p in values]
    payload = "\0".join([directory, *names, "", ""])
    if len(payload.encode("utf-16-le")) > 65532:
        raise ValueError("所选文件太多，请分次添加。")
    return payload.encode("utf-16-le").hex()


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("wecom_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guard_command(args):
    environment = dict(os.environ, WINEPREFIX=str(args.prefix),
                       WINEDEBUG="-all")
    original = ["/usr/bin/wine", str(args.guard)]
    if args.test_exe:
        production = Path.home() / (
            ".local/share/wecom-hyprland/prefix-dpi96-trial")
        if args.prefix.resolve() == production.resolve():
            raise ValueError("测试必须使用独立前缀")
        return original + ["--test-exe", args.test_exe], environment
    installed = Path(__file__).with_name("installed.json")
    record = json.loads(installed.read_text())
    if args.guard.resolve() != Path(__file__).with_name("guard.exe").resolve():
        raise ValueError("正式运行只能使用已安装的文件选择桥接")
    for name, expected in record["files"].items():
        if name not in ("guard.exe", "bridge.py"):
            raise ValueError("文件选择桥接的安装记录无效")
        data = installed.with_name(name).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ValueError("文件选择桥接文件已改变，请重新验证")
    if set(record["files"]) != {"guard.exe", "bridge.py"}:
        raise ValueError("文件选择桥接的安装记录不完整")
    runtime_path = (Path.home() / ".local/share/wecom-hyprland" /
                    "wine-runtime-compat/launch-existing.py")
    runtime = load_runtime(runtime_path)
    policy = json.loads(runtime_path.with_name("installed.json").read_text())
    if args.prefix != Path(policy["prefix"]):
        raise ValueError("文件选择桥接只适用于已验证的企业微信前缀")
    running = runtime.server_hash(args.prefix)
    if os.environ.get(runtime.MARKER):
        runtime.check_view(policy, os.environ)
        profile_name = os.environ[runtime.MARKER]
        root = Path("/")
        command = original
    else:
        identity = {name: runtime.digest("/" + name)
                    for name in runtime.IDENTITY}
        profile_name, _ = runtime.choose(policy, identity, running)
        profile = policy["profiles"][profile_name]
        runtime.verify_bundle(profile)
        root = Path(profile["root"])
        command = runtime.command_for(profile, original)
        environment.update(WINELOADER="/usr/bin/wine",
                           WINESERVER="/usr/bin/wineserver")
    if profile_name != "wine-11.22-1":
        raise ValueError("文件选择桥接尚未验证此 Wine 版本，保留原窗口")
    for arch, expected in COMDLG_HASHES.items():
        library = root / "usr/lib/wine" / arch / "comdlg32.dll"
        if runtime.digest(library) != expected:
            raise ValueError("文件选择组件已改变，保留原窗口")
    return command, environment


class Bridge:
    def __init__(self, args, command, environment):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
        self.Gdk, self.GLib, self.Gtk = Gdk, GLib, Gtk
        self.args = args
        self.dialog = None
        self.request = None
        self.last_folder = None
        self.filters = []
        self.buffer = b""
        self.out_buffer = b""
        self.writer = None
        self.test_count = 0
        self.shutting_down = False
        self.child = subprocess.Popen(command, env=environment,
                                      stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE)
        os.set_blocking(self.child.stdout.fileno(), False)
        os.set_blocking(self.child.stdin.fileno(), False)
        GLib.io_add_watch(self.child.stdout.fileno(), GLib.IO_IN | GLib.IO_HUP,
                         self.read_events)
        GLib.timeout_add(1000, self.check_child)
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.shutdown)

    def send(self, action, suffix=""):
        if self.request is None:
            return
        request = self.request
        line = f"{action} {request['hwnd']} {request['pid']}{suffix}\n"
        self.out_buffer += line.encode("ascii")
        if self.writer is None:
            self.writer = self.GLib.io_add_watch(
                self.child.stdin.fileno(),
                self.GLib.IO_OUT | self.GLib.IO_HUP, self.write_commands)

    def write_commands(self, fd, condition):
        try:
            count = os.write(fd, self.out_buffer)
            self.out_buffer = self.out_buffer[count:]
            if self.out_buffer:
                return True
            self.writer = None
            return False
        except BlockingIOError:
            return True
        except (OSError, BrokenPipeError):
            self.writer = None
            self.shutdown()
            return False

    def read_events(self, fd, condition):
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return True
        if not data:
            self.shutdown()
            return False
        self.buffer += data
        if len(self.buffer) > 1024 * 1024:
            self.shutdown()
            return False
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
                if event.get("event") == "open":
                    self.open_dialog(event)
                elif self.request and event.get("hwnd") == self.request["hwnd"]:
                    if event.get("event") in ("closed", "fallback"):
                        self.destroy_dialog()
                # Logs contain event kinds, never selected filenames.
                print("file-picker: " + str(event.get("event", "unknown")),
                      flush=True)
            except (ValueError, KeyError, TypeError, OSError) as error:
                print(f"file-picker: event rejected "
                      f"({type(error).__name__})", file=sys.stderr)
                self.send("fallback")
                self.destroy_dialog()
        return True

    def open_dialog(self, event):
        if self.dialog:
            self.send("fallback")
            self.destroy_dialog()
        Gtk = self.Gtk
        self.request = event
        if self.args.test_exe:
            print("file-picker test: " + json.dumps(event), flush=True)
        dialog = Gtk.FileChooserDialog(title="企业微信 — 选择文件",
                                       action=Gtk.FileChooserAction.OPEN)
        self.dialog = dialog
        dialog.set_wmclass("wecom-native-file-picker", "WecomNativeFilePicker")
        dialog.add_button("使用原窗口", 1001)
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        accept = (event.get("accept") or "打开").replace("&", "")
        dialog.add_button(accept, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_select_multiple(bool(event.get("multi")))
        dialog.set_local_only(True)
        dialog.set_modal(True)
        dialog.set_default_size(960, 660)
        self.filters = []
        for spec in event.get("filters", [])[:100]:
            filter_ = Gtk.FileFilter()
            filter_.set_name(spec["name"])
            patterns = tuple("*" if p in ("*.*", "*") else p.casefold()
                             for p in spec["pattern"].split(";"))
            filter_.add_custom(Gtk.FileFilterFlags.FILENAME,
                               lambda info, masks: bool(info.filename) and any(
                                   fnmatch.fnmatchcase(
                                       Path(info.filename).name.casefold(),
                                       mask)
                                   for mask in masks), patterns)
            dialog.add_filter(filter_)
            self.filters.append(filter_)
        if self.filters:
            index = min(max(int(event.get("filter_index", 1)) - 1, 0),
                        len(self.filters) - 1)
            dialog.set_filter(self.filters[index])
        initial = None
        for key in ("current_dir", "initial_dir"):
            candidate = windows_to_local(event.get(key, ""), self.args.prefix)
            if candidate and candidate.is_dir():
                initial = candidate
                break
        if initial is None:
            initial = self.last_folder or Path.home()
        dialog.set_current_folder(str(initial))
        dialog.connect("response", self.response)
        dialog.connect("map", self.mapped)
        dialog.show_all()
        dialog.present()

    def mapped(self, dialog):
        self.send("hide")
        self.GLib.timeout_add(100, self.present)
        if self.args.test_exe and self.args.test_action:
            self.GLib.timeout_add(600, self.test_action)

    def present(self):
        if self.dialog:
            self.dialog.present()
        return False

    def test_action(self):
        if not self.dialog:
            return False
        self.test_count += 1
        if self.args.test_action == "cancel":
            self.dialog.response(self.Gtk.ResponseType.CANCEL)
        elif self.args.test_action == "fallback":
            self.dialog.response(1001)
        else:
            if self.args.test_filter_index and self.filters:
                self.dialog.set_filter(
                    self.filters[self.args.test_filter_index - 1])
            self.dialog.set_current_folder(
                str(Path(self.args.test_file[0]).parent))
            self.GLib.timeout_add(600, self.test_select)
        return False

    def test_select(self):
        if self.dialog:
            self.dialog.unselect_all()
            if len(self.args.test_file) > 1:
                self.dialog.select_all()
            else:
                self.dialog.select_filename(self.args.test_file[0])
            self.GLib.timeout_add(300, self.test_accept)
        return False

    def test_accept(self):
        if self.dialog:
            if set(self.dialog.get_filenames()) != set(self.args.test_file):
                print("file-picker test: GTK selection incomplete",
                      file=sys.stderr, flush=True)
                print(self.dialog.get_filenames(), file=sys.stderr, flush=True)
                self.send("fallback")
                self.destroy_dialog()
                return False
            self.dialog.response(self.Gtk.ResponseType.ACCEPT)
        return False

    def response(self, dialog, response):
        Gtk = self.Gtk
        folder = dialog.get_current_folder()
        if folder and Path(folder).is_dir():
            self.last_folder = Path(folder)
        if response == Gtk.ResponseType.ACCEPT:
            try:
                paths = dialog.get_filenames()
                if len(paths) > 1 and not self.request.get("multi"):
                    raise ValueError("这个入口只支持选择一个文件。")
                payload = selection_payload(paths, self.args.prefix)
                selected = dialog.get_filter()
                index = (self.filters.index(selected) + 1
                         if selected in self.filters else 0)
            except ValueError as error:
                message = Gtk.MessageDialog(transient_for=dialog, modal=True,
                                            message_type=Gtk.MessageType.INFO,
                                            buttons=Gtk.ButtonsType.OK,
                                            text=str(error))
                message.run()
                message.destroy()
                return
            self.send("select", f" {index} {payload}")
        elif response == 1001:
            self.send("fallback")
        else:
            self.send("cancel")
        self.destroy_dialog(clear_request=False)

    def destroy_dialog(self, clear_request=True):
        if self.dialog:
            dialog, self.dialog = self.dialog, None
            dialog.destroy()
        if clear_request:
            self.request = None

    def check_child(self):
        if self.child.poll() is not None:
            self.shutdown()
            return False
        return True

    def shutdown(self):
        if self.shutting_down:
            return False
        self.shutting_down = True
        self.destroy_dialog()
        if self.writer is not None:
            self.GLib.source_remove(self.writer)
            self.writer = None
        try:
            self.child.stdin.close()
        except OSError:
            pass
        self.Gtk.main_quit()
        return False

    def run(self):
        self.Gtk.main()
        try:
            self.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # Closing the pipe requests graceful restoration by the guard.
            print("file-picker: guard still restoring its window",
                  file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--guard", type=Path,
                        default=Path(__file__).with_name("guard.exe"))
    parser.add_argument("--test-exe")
    parser.add_argument("--test-action",
                        choices=("select", "cancel", "fallback"))
    parser.add_argument("--test-file", action="append", default=[])
    parser.add_argument("--test-filter-index", type=int)
    args = parser.parse_args()
    args.prefix = args.prefix.resolve()
    if args.test_action and not args.test_exe:
        parser.error("自动操作只允许独立测试程序")
    os.umask(0o077)
    os.environ["GDK_BACKEND"] = "x11"
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR",
                                     f"/run/user/{os.getuid()}"))
    key = hashlib.sha256(str(args.prefix).encode()).hexdigest()[:16]
    lock_path = runtime_dir / ("wecom-file-picker-" + key + ".lock")
    lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    command, environment = guard_command(args)
    Bridge(args, command, environment).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
