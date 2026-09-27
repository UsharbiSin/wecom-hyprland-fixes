#!/usr/bin/env python3
"""读取当前用户的目录请求；仅调用 Thunar，不执行请求内容。"""
import fcntl
import os
from pathlib import Path
import stat
import struct
import subprocess
import time

MAX_RECORD = 32768
MAX_QUEUE = 8 * 1024 * 1024


def secure_open(path):
    """拒绝符号链接、其他用户、非普通文件和宽松权限。"""
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    valid = stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
    valid = valid and stat.S_IMODE(info.st_mode) == 0o600
    valid = valid and info.st_nlink == 1
    if not valid:
        os.close(fd)
        raise PermissionError("队列必须是当前用户所有、0600 的独立普通文件")
    return fd


def parse_record(stream):
    """不完整尾记录保留位置，等待 Wine 完成追加。"""
    start = stream.tell()
    header = stream.read(4)
    if len(header) < 4:
        stream.seek(start)
        return None
    length, = struct.unpack("<I", header)
    if not 0 < length <= MAX_RECORD:
        raise ValueError("目录队列的记录长度无效")
    payload = stream.read(length)
    if len(payload) != length:
        stream.seek(start)
        return None
    try:
        path = Path(payload.decode("utf-8"))
    except (UnicodeError, ValueError):
        return False
    if b"\0" in payload or not path.is_absolute():
        return False
    if path.is_file():
        path = path.parent
    return path if path.is_dir() else False


def main():
    value = os.environ.get("WECOM_THUNAR_QUEUE", "")
    path = Path(value)
    if not value or not path.is_absolute():
        raise ValueError("WECOM_THUNAR_QUEUE 必须是绝对路径")
    parent = path.parent
    info = parent.stat()
    if parent.is_symlink() or info.st_uid != os.getuid():
        raise PermissionError("队列父目录必须由当前用户所有且不能是符号链接")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("队列父目录必须禁止其他用户访问")
    fd = secure_open(path)
    with os.fdopen(fd, "r+b", buffering=0) as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # 不重放上次会话的私人目录请求。
        stream.seek(0, os.SEEK_END)
        while True:
            if os.fstat(stream.fileno()).st_size > MAX_QUEUE + MAX_RECORD:
                raise ValueError("目录队列过大，请关闭企业微信后清理队列")
            record = parse_record(stream)
            if record is None:
                time.sleep(0.05)
            elif record:
                subprocess.Popen(
                    ["thunar", str(record)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )


if __name__ == "__main__":
    main()
