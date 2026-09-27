"""在同一 bwrap 文件视图中运行企业微信及辅助进程。"""
import json
from pathlib import Path
import subprocess
import sys


def stop_helpers(children):
    # 仅回收本次创建的辅助进程；不得用 wineserver -k 结束整个前缀。
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run(specification):
    children = []
    try:
        for command in specification["helpers"]:
            children.append(subprocess.Popen(command))
        app = specification["app"]
        result = subprocess.run(
            ["wine", app] + specification["args"], cwd=str(Path(app).parent),
        )
        # 企业微信可能让启动进程退出，由子进程继续工作。
        # Win32 辅助进程均有目标退出监视和启动超时，不永久占用服务。
        subprocess.run(["wineserver", "-w"], check=True)
        return result.returncode
    finally:
        stop_helpers(children)


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    raise SystemExit(run(data))
