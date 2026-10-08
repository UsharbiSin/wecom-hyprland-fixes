"""Manage one CopyQ rule with native objects, never clipboard writes."""
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "wecom_image_history_png_v1"
RULE_NAME = "企业微信图片历史预览（专用）"

SNAPSHOT = """
var request = JSON.parse(str(input()));
var current = commands();
JSON.stringify({
    names: current.map(function(c) { return c.name; }),
    exported: exportCommands(current),
    rule_exports: current.filter(function(c) {
        return c.internalId === request.rule_id;
    }).map(function(c) { return exportCommands([c]); }),
    unrelated_export: exportCommands(current.filter(function(c) {
        return c.internalId !== request.rule_id;
    }))
});
"""

# Native RegExp wrappers must never round-trip through JSON. Only the new,
# minimal raw rule arrives as JSON; all existing command objects stay native.
APPLY = """
var request = JSON.parse(str(input()));
var current = commands();
if (exportCommands(current) !== request.expected)
    throw new Error("CopyQ commands changed; nothing was written");
var wanted = current.filter(function(c) {
    return c.internalId !== request.rule_id;
});
var unrelated = exportCommands(wanted);
if (request.operation === "install") wanted.unshift(request.rule);
var expected = exportCommands(wanted);
setCommands(wanted);
var after = commands();
if (exportCommands(after) !== expected)
    throw new Error("Post-save export differs; retain recovery record");
var rest = after.filter(function(c) {
    return c.internalId !== request.rule_id;
});
if (exportCommands(rest) !== unrelated)
    throw new Error("Unrelated commands changed; retain recovery record");
JSON.stringify({
    unrelated_export: exportCommands(rest),
    rule_exports: after.filter(function(c) {
        return c.internalId === request.rule_id;
    }).map(function(c) { return exportCommands([c]); })
});
"""


def state_dir():
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "wecom-fixes" / "copyq"


def source():
    return ROOT / "modules/copyq/wecom-image-history.js"


def rule_definition():
    return {
        "name": RULE_NAME, "internalId": RULE_ID,
        "automatic": True, "enable": True, "inMenu": False,
        "input": "text/html",
        "cmd": "copyq:\n" + source().read_text(encoding="utf-8"),
    }


class Client:
    def __init__(self, command=None, environment=None):
        self.command = command or ["copyq"]
        self.environment = environment

    def call(self, script, payload):
        # '--' prevents CopyQ from unescaping JavaScript command arguments.
        try:
            result = subprocess.run(
                self.command + ["eval", "--", script],
                input=json.dumps(payload, ensure_ascii=False),
                text=True, capture_output=True, check=True, timeout=30,
                env=self.environment,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("CopyQ 请求超时；请检查服务及安装记录。") from error
        except subprocess.CalledProcessError as error:
            raise ValueError("CopyQ 请求失败：" + error.stderr.strip()) \
                from error
        return json.loads(result.stdout)

    def snapshot(self):
        return self.call(SNAPSHOT, {"rule_id": RULE_ID})

    def exported_rule(self, rule):
        return self.call(
            "JSON.stringify(exportCommands([JSON.parse(str(input()))]));",
            rule,
        )

    def apply(self, before, operation, rule=None):
        return self.call(APPLY, {
            "expected": before["exported"], "rule_id": RULE_ID,
            "operation": operation, "rule": rule,
        })


@contextlib.contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    with (directory / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("CopyQ 模块正在修改，请稍后重试。") from error
        yield


def save(path, text):
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_record(directory):
    path = directory / "installed.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_record(directory, record):
    save(directory / "installed.json",
         json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def verify_after(before, after, expected):
    if (after["unrelated_export"] != before["unrelated_export"]
            or after["rule_exports"] != expected):
        raise ValueError("CopyQ 保存后校验失败；已保留备份和恢复记录。")


def install(directory, client, dry_run=False):
    rule = rule_definition()
    desired = client.exported_rule(rule)
    before = client.snapshot()
    record = read_record(directory)
    if before["rule_exports"]:
        if (record and record["status"] in ("prepared", "installed")
                and record["installed_rule_export"] == desired
                and before["rule_exports"] == [desired]):
            if not dry_run:
                record["status"] = "installed"
                write_record(directory, record)
            print("CopyQ 专用规则已安装且一致；无需修改。")
            return
        raise ValueError("已有专用规则或内容冲突；请先用原安装方式移除。")
    if RULE_NAME in before["names"]:
        raise ValueError("同名 CopyQ 规则已存在；没有覆盖。")
    if record and record["status"] == "installed":
        raise ValueError("记录中的规则已丢失；请先 remove 核对移除状态。")
    print("安装计划：为 CopyQ 新图片历史添加预览；保留其他命令。")
    if dry_run:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = directory / ("commands-" + stamp + "-" + uuid.uuid4().hex[:8])
    backup = backup.with_suffix(".ini")
    save(backup, before["exported"])
    content = source().read_bytes()
    save(directory / "wecom-image-history.js", content.decode("utf-8"))
    record = {
        "status": "prepared", "backup": backup.name,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "installed_rule_export": desired,
    }
    write_record(directory, record)
    after = client.apply(before, "install", rule)
    verify_after(before, after, [desired])
    record["status"] = "installed"
    write_record(directory, record)
    print(f"CopyQ 规则已安装；原生命令备份：{backup}")


def remove(directory, client):
    record = read_record(directory)
    if not record or record["status"] == "removed":
        print("没有本工具管理的 CopyQ 规则，无需移除。")
        return
    before = client.snapshot()
    if before["rule_exports"]:
        if before["rule_exports"] != [record["installed_rule_export"]]:
            raise ValueError("CopyQ 专用规则已被修改或重复；没有删除。")
        after = client.apply(before, "remove")
        verify_after(before, after, [])
    record["status"] = "removed"
    write_record(directory, record)
    print("CopyQ 专用规则已移除；其他规则、历史及备份均保留。")


def doctor(directory=None, client=None):
    directory = directory or state_dir()
    record = read_record(directory)
    if not record or record["status"] == "removed":
        return
    if record["status"] != "installed":
        raise ValueError("CopyQ 上次安装未完成，请 install 重试或 remove。")
    installed = directory / "wecom-image-history.js"
    if hashlib.sha256(installed.read_bytes()).hexdigest() != \
            record["source_sha256"]:
        raise ValueError("CopyQ 规则源码副本已改变，请核对安装目录。")
    snapshot = (client or Client()).snapshot()
    if snapshot["rule_exports"] != [record["installed_rule_export"]]:
        raise ValueError("CopyQ 专用规则缺失、重复或已修改，请核对命令设置。")
    print("校验通过：copyq（原生规则与安装记录一致）")


def manage(action, dry_run=False):
    if action == "build":
        source().read_text(encoding="utf-8")
        print("CopyQ 模块无需编译；可直接 install copyq。")
        return
    directory = state_dir()
    if action == "install" and dry_run:
        install(directory, Client(), dry_run=True)
        return
    with locked(directory):
        if action == "install":
            install(directory, Client())
        elif action == "remove":
            remove(directory, Client())
        else:
            raise ValueError("不支持的 CopyQ 模块操作。")
