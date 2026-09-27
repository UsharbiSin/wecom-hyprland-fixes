"""管理独立修复模块；不覆盖系统 Wine 或原有启动器。"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROC = Path("/proc")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def modules():
    return {
        p.parent.name: read_json(p)
        for p in sorted((ROOT / "modules").glob("*/module.json"))
    }


def prefix_path(raw):
    if not raw:
        raise ValueError("请用 --prefix 明确指定企业微信专用 Wine 前缀。")
    path = Path(raw).expanduser().resolve()
    if not (path / "user.reg").is_file():
        raise ValueError("目标不是已初始化的 Wine 前缀。")
    return path


def state_path(prefix):
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    key = hashlib.sha256(os.fsencode(prefix)).hexdigest()[:16]
    return base / "wecom-fixes" / key


def variables(prefix, version, module_id):
    state = state_path(prefix)
    return {
        "prefix": str(prefix),
        "app_version": version,
        "app_dir": str(prefix / "drive_c/Program Files (x86)/WXWork"),
        "state_dir": str(state),
        "module_dir": str(state / "modules" / module_id),
    }


def check_state_path(module, context):
    directory = context["module_dir"]
    if module.get("preload") and any(
            char.isspace() or char == ":" for char in directory):
        raise ValueError("共享库安装路径含空白或冒号，无法用于 LD_PRELOAD。")
    if module.get("path") and ":" in directory:
        raise ValueError("桌面模块安装路径含冒号，无法加入 PATH。")


def expand(value, context):
    return value.format_map(context)


def process_prefix(environment):
    values = dict(item.split(b"=", 1) for item in environment
                  if b"=" in item)
    raw = values.get(b"WINEPREFIX")
    if raw:
        return Path(os.fsdecode(raw)).expanduser().resolve()
    home = values.get(b"HOME", os.fsencode(Path.home()))
    return (Path(os.fsdecode(home)) / ".wine").resolve()


def stopped(prefix):
    # 同一前缀的别名路径、默认前缀以及 DLL 会议宿主也必须被识别。
    prefix = prefix.resolve()
    for path in PROC.glob("[0-9]*/environ"):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            env = path.read_bytes().split(b"\0")
            first = (path.parent / "cmdline").read_bytes().split(b"\0")[0]
            first = first.lower().replace(b"\\", b"/")
            name = first.rsplit(b"/", 1)[-1]
            executable = (path.parent / "exe").resolve().name.lower()
        except (OSError, PermissionError):
            continue
        is_wine = (
            executable.startswith("wine") or name.startswith(b"wine")
            or name.endswith(b".exe")
            or (first.startswith(b"--module=") and name.endswith(b".dll"))
        )
        if is_wine and process_prefix(env) == prefix:
            raise ValueError("该前缀仍有 Wine 进程，请先正常退出企业微信。")


@contextlib.contextmanager
def locked(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    with (state / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("该前缀正在运行或修改，请等待其正常结束。") from error
        yield


def finish_registry(prefix):
    # reg 已同步退出；只等待其短命服务结束，不杀用户可能刚启动的应用。
    try:
        subprocess.run(
            ["wineserver", "-w"], env=wine_env(prefix), check=True, timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Wine 服务未退出；保留恢复记录，请先正常关闭应用。") \
            from error


def atomic_copy(source, target):
    target = Path(target)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".wecom-fix-", dir=target.parent,
    )
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def wine_env(prefix):
    result = os.environ.copy()
    result.update(WINEPREFIX=str(prefix), WINEDEBUG="-all")
    return result


def normalize_entry(entry):
    result = dict(entry)
    if isinstance(result["key"], list):
        result["key"] = "".join(result["key"])
    return result


def reg_read(prefix, entry):
    entry = normalize_entry(entry)
    command = ["wine", "reg", "query", entry["key"], "/v", entry["name"]]
    result = subprocess.run(
        command, env=wine_env(prefix), capture_output=True, text=True,
    )
    if result.returncode:
        error = result.stdout + result.stderr
        if "Unable to find" in error or "找不到" in error:
            return None
        raise ValueError("读取注册表失败，未修改；请先检查 Wine 是否可用。")
    for line in result.stdout.splitlines():
        fields = re.split(r"\s{2,}", line.strip(), maxsplit=2)
        if len(fields) == 3 and fields[0] == entry["name"]:
            return {"type": fields[1], "value": fields[2]}
    raise ValueError("无法识别注册表结果，拒绝覆盖。")


def reg_write(prefix, entry, value):
    entry = normalize_entry(entry)
    command = ["wine", "reg"]
    if value is None:
        command += ["delete", entry["key"], "/v", entry["name"], "/f"]
    else:
        command += [
            "add", entry["key"], "/v", entry["name"],
            "/t", value["type"], "/d", value["value"], "/f",
        ]
    subprocess.run(command, env=wine_env(prefix), check=True)


def equivalent(a, b):
    if a is None or b is None:
        return a == b
    if a["type"] != b["type"]:
        return False
    if a["type"] == "REG_DWORD":
        return int(a["value"], 0) == int(b["value"], 0)
    return a["value"] == b["value"]


def check_targets(module, context):
    for item in module.get("guards", []) + module.get("bindings", []):
        target = Path(expand(item["target"], context))
        if not target.is_file():
            raise ValueError(f"缺少目标文件：{target}")
        if item.get("sha256") and digest(target) != item["sha256"]:
            raise ValueError(f"版本哈希不匹配：{target.name}；请重新验证。")


def build(args, module):
    prefix = prefix_path(args.prefix)
    output = ROOT / "build" / module["id"]
    output.parent.mkdir(exist_ok=True)
    for name in module.get("dependencies", []):
        if not shutil.which(name):
            raise ValueError(f"缺少依赖命令：{name}")
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        env = os.environ.copy()
        env.update(
            WECOM_BUILD_DIR=temporary,
            WECOM_PREFIX=str(prefix),
            WECOM_APP_VERSION=args.app_version,
            WECOM_WINE_SOURCE=args.wine_source or "",
        )
        subprocess.run(
            ["bash", "build.sh"], cwd=ROOT / "modules" / module["id"],
            env=env, check=True,
        )
        files = {}
        for name in module["artifacts"]:
            file = Path(temporary) / name
            if not file.is_file() or file.is_symlink():
                raise ValueError(f"构建缺少常规文件：{name}")
            files[name] = digest(file)
        write_json(Path(temporary) / "build.json", {
            "prefix": str(prefix), "app_version": args.app_version,
            "files": files, "module": module,
        })
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(temporary, output)
    print(f"构建完成：{output}")


def install(args, module):
    prefix = prefix_path(args.prefix)
    source = ROOT / "build" / module["id"]
    manifest = read_json(source / "build.json")
    if manifest["prefix"] != str(prefix):
        raise ValueError("构建前缀与安装前缀不同，请重新构建。")
    if manifest["app_version"] != args.app_version:
        raise ValueError("构建版本与安装版本不同，请重新构建。")
    if manifest["module"] != module:
        raise ValueError("模块定义已经改变，请重新构建。")
    for name, sha in manifest["files"].items():
        if digest(source / name) != sha:
            raise ValueError(f"构建产物已改变：{name}")
    context = variables(prefix, args.app_version, module["id"])
    check_state_path(module, context)
    check_targets(module, context)
    print(f"安装计划：{module['title']} → {context['module_dir']}")
    for entry in module.get("registry", []):
        print(f"注册表：{entry['key']} / {entry['name']}={entry['value']}")
    for item in module.get("copies", []):
        target = Path(expand(item["target"], context))
        if not target.parent.is_dir():
            raise ValueError("会议版本目录不存在。")
        if target.exists() and digest(target) != item.get("replace_sha256"):
            raise ValueError("兼容库位置已有未知文件，拒绝覆盖。")
        print(f"新增兼容库：{target}")
    if args.dry_run:
        return
    state = state_path(prefix)
    with locked(state):
        stopped(prefix)
        check_state_path(module, context)
        check_targets(module, context)
        destination = Path(context["module_dir"])
        if destination.exists():
            raise ValueError("模块已安装；先 remove，再构建和安装新版。")
        destination.parent.mkdir(exist_ok=True)
        shutil.copytree(source, destination)
        saved = []
        record = dict(manifest)
        record["targets"] = {
            expand(item["target"], context): digest(
                expand(item["target"], context)
            )
            for item in module.get("bindings", []) + module.get("guards", [])
        }
        record["registry"] = saved
        record["copies"] = []
        record["ready"] = False
        write_json(destination / "installed.json", record)
        registry_started = False
        try:
            for item in module.get("copies", []):
                target = Path(expand(item["target"], context))
                if target.is_symlink():
                    raise ValueError("新增兼容库的目标不能为符号链接。")
                previous = None
                if target.exists():
                    if digest(target) != item.get("replace_sha256"):
                        raise ValueError("兼容库位置已有未知文件，拒绝覆盖。")
                    previous = f"backup-{len(record['copies'])}-"
                    previous += target.name
                    shutil.copy2(target, destination / previous)
                elif not target.parent.is_dir():
                    raise ValueError("会议版本目录不存在。")
                record["copies"].append({
                    "target": str(target), "previous": previous,
                    "sha256": digest(destination / item["source"]),
                    "previous_sha256": (
                        digest(target) if previous else None
                    ),
                })
                write_json(destination / "installed.json", record)
                atomic_copy(destination / item["source"], target)
            for entry in module.get("registry", []):
                registry_started = True
                previous = reg_read(prefix, entry)
                saved.append({"entry": entry, "previous": previous})
                write_json(destination / "installed.json", record)
                reg_write(prefix, entry, entry)
            record["ready"] = True
            write_json(destination / "installed.json", record)
        finally:
            if registry_started:
                finish_registry(prefix)
    print("安装完成。使用本仓库 launch 启动才会加载文件修复。")


def installed(prefix):
    return sorted((state_path(prefix) / "modules").glob("*/installed.json"))


def verify_record(path):
    record = read_json(path)
    if not record.get("ready"):
        raise ValueError("上次安装未完成，请先 remove 回滚。")
    for name, sha in record["files"].items():
        if digest(path.parent / name) != sha:
            raise ValueError(f"修复产物校验失败：{name}")
    for target, sha in record["targets"].items():
        if digest(target) != sha:
            raise ValueError(f"目标版本已改变：{Path(target).name}")
    for item in record.get("copies", []):
        if digest(item["target"]) != item["sha256"]:
            raise ValueError("已安装兼容库发生变化，请人工核对。")
    return record


def check_copies_for_rollback(record, folder):
    for item in record.get("copies", []):
        target = Path(item["target"])
        if target.is_symlink():
            raise ValueError("兼容库目标变成符号链接，拒绝回滚。")
        if target.exists() and digest(target) not in (
                item["sha256"], item["previous_sha256"]):
            raise ValueError("兼容库被后续修改，拒绝覆盖。")
        if item["previous"]:
            backup = folder / item["previous"]
            if backup.is_symlink() or (
                    digest(backup) != item["previous_sha256"]):
                raise ValueError("兼容库备份损坏，拒绝回滚。")


def remove(args):
    prefix = prefix_path(args.prefix)
    state = state_path(prefix)
    with locked(state):
        stopped(prefix)
        path = state / "modules" / args.module / "installed.json"
        record = read_json(path)
        # 先检查全部冲突，再恢复；不同模块的注册表值不得重叠。
        check_copies_for_rollback(record, path.parent)
        changes = record.get("registry", [])
        try:
            for item in changes:
                current = reg_read(prefix, item["entry"])
                if not equivalent(current, item["entry"]) and not equivalent(
                        current, item["previous"]):
                    raise ValueError("注册表被后续修改，拒绝自动覆盖。")
            for item in reversed(changes):
                if not equivalent(reg_read(prefix, item["entry"]),
                                  item["previous"]):
                    reg_write(prefix, item["entry"], item["previous"])
        finally:
            if changes:
                finish_registry(prefix)
        for item in reversed(record.get("copies", [])):
            target = Path(item["target"])
            if item["previous"]:
                atomic_copy(path.parent / item["previous"], target)
            elif target.exists():
                target.unlink()
        shutil.rmtree(path.parent)
    print("模块已移除，原文件未被覆盖，注册表已恢复。")


def doctor(args):
    prefix = prefix_path(args.prefix)
    failed = False
    for path in installed(prefix):
        try:
            verify_record(path)
            print(f"校验通过：{path.parent.name}")
        except (ValueError, OSError) as error:
            failed = True
            print(f"不可加载：{path.parent.name}：{error}")
    print("这里只检查安装状态和哈希；真实界面验收请按各模块文档操作。")
    if failed:
        raise ValueError("存在不可加载模块；请回滚或重新验证版本。")


def launch(args):
    prefix = prefix_path(args.prefix)
    # 父进程持锁覆盖整个会话，避免运行时并行安装、卸载或二次启动。
    with locked(state_path(prefix)):
        stopped(prefix)
        return launch_locked(args, prefix)


def launch_locked(args, prefix):
    env = wine_env(prefix)
    env["WINEDLLOVERRIDES"] = ";".join(filter(None, [
        env.get("WINEDLLOVERRIDES"), "winewayland.drv=d",
    ]))
    bindings = []
    helpers = []
    preloads = []
    for path in installed(prefix):
        record = verify_record(path)
        module = record["module"]
        context = variables(prefix, record["app_version"], module["id"])
        check_state_path(module, context)
        for key, value in module.get("environment", {}).items():
            env[key] = expand(value, context)
        if module.get("path"):
            value = path.parent / module["path"]
            env["PATH"] = str(value) + os.pathsep + env["PATH"]
        if module.get("preload"):
            preloads.append(str(path.parent / module["preload"]))
        for item in module.get("bindings", []):
            bindings += [
                "--ro-bind", str(path.parent / item["source"]),
                expand(item["target"], context),
            ]
        for helper in module.get("helpers", []):
            helpers.append([
                helper["runner"], str(path.parent / helper["source"]),
            ] + [expand(v, context) for v in helper.get("args", [])])
    if preloads:
        env["LD_PRELOAD"] = ":".join(preloads + list(filter(None, [
            env.get("LD_PRELOAD"),
        ])))
    for key in ("GTK_IM_MODULE", "QT_IM_MODULE", "SDL_IM_MODULE"):
        env.setdefault(key, "fcitx")
    env.setdefault("XMODIFIERS", "@im=fcitx")
    app = prefix / "drive_c/Program Files (x86)/WXWork/WXWork.exe"
    if not app.is_file():
        raise ValueError("专用前缀中未找到 WXWork.exe。")
    for name in ("wine", "bwrap"):
        if not shutil.which(name):
            raise ValueError(f"缺少启动命令：{name}")
    state = state_path(prefix)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    # helpers 与应用一起处于同一文件视图，wineserver 首次也在视图内启动。
    specification = state / "launch.json"
    write_json(specification, {
        "helpers": helpers, "app": str(app), "args": args.app_args,
    })
    command = ["bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev"]
    command += bindings + [
        sys.executable, str(ROOT / "tools/session.py"), str(specification),
    ]
    result = subprocess.run(command, env=env, check=False)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)


def main():
    parser = argparse.ArgumentParser(description="企业微信分项修复管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出模块")
    for command in ("build", "install", "remove", "doctor", "launch"):
        child = commands.add_parser(command)
        if command in ("build", "install", "remove"):
            child.add_argument("module", choices=sorted(modules()))
        child.add_argument("--prefix", help="企业微信专用 Wine 前缀")
        child.add_argument("--app-version", default="5.0.11.6018")
        if command == "build":
            child.add_argument("--wine-source")
        if command == "install":
            child.add_argument("--dry-run", action="store_true")
        if command == "launch":
            child.add_argument("app_args", nargs="*")
    args = parser.parse_args()
    try:
        if args.command == "list":
            for ident, module in modules().items():
                print(f"{ident:12} {module['title']}")
        elif args.command in ("build", "install"):
            globals()[args.command](args, modules()[args.module])
        else:
            globals()[args.command](args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"操作停止：{error}", file=sys.stderr)
        raise SystemExit(1) from error
