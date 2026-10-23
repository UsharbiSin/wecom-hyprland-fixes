# Fcitx 重启后的 XIM 重连修复

本模块修复 libX11 在同一进程使用多个 X11 `Display` 连接时，
输入法发现回调注册和移除错误的问题。典型症状是企业微信运行期间
Fcitx 重启后，英文和数字仍能输入，中文候选不再出现；重新启动企业微信暂时恢复。

这里的多个 `Display` 指同一进程内的多个 X11 客户端连接，
不是多显示器。Wine 的不同窗口线程可以各自拥有这样的连接。

## 原因与修复

现场诊断发现，失灵窗口所属 Wine 线程的 XIM 指针为空，
其他线程仍有有效 XIM 连接。Fcitx 重启后，Wine 需要通过
`XRegisterIMInstantiateCallback` 等待输入法重新出现。

libX11 1.8.13 的 `modules/im/ximcp/imInsClbk.c` 把所有连接的回调放在
一个进程级链表中，却只给第一个回调的连接安装根窗口属性事件过滤器。
删除链表首项时还会直接卸载过滤器，即使该连接仍有其他回调。
过滤器遍历回调时也没有核对所属连接。因此，输入法重新公告时，
某个连接可能收不到通知，或由另一个连接错误地触发它的回调。

[源码补丁](xim-instantiate-per-display.patch) 仅调整这条发现回调路径：

1. 每个连接注册第一个有效回调时安装过滤器。
2. 只分派属于当前连接的回调，注销时也匹配连接。
3. 连接的最后一个有效回调移除后才卸载过滤器。
4. 回调内延迟注销沿用同样的清理规则，并释放回调持有的资源。

补丁没有修改 Wine 私有结构偏移，也不修改输入内容。
它解决输入法重新发现机制；现存进程已加载的旧库仍需通过退出并重新启动来更换。

## 版本与部署范围

| 项目 | 当前定位及回归范围 |
| --- | --- |
| 系统库 | Arch Linux libX11 1.8.13-2，64 位 |
| 重建源码 | 官方 libX11 1.8.13 |
| 现场运行环境 | Wine 11.19、Fcitx5 5.1.23、Hyprland/Xwayland |
| 绑定目标 | `/usr/lib/libX11.so.6.4.0` |

系统原库 SHA-256：
`8d818805efd9e4c08d44981093f99afa2641f3dbefd70770788a570fb7ef8ecf`

安装器检查原库的精确哈希。相同版本号不保证文件字节相同；
系统库升级后应重新验证，不应删去检查来强行加载旧补丁。

补丁库通过 `bwrap --ro-bind` 进入启动器及其子进程的文件视图，
磁盘上的系统 libX11 保持原样。已从其他入口运行的程序不受该绑定影响。
启动器发起的其他子进程也会继承这个文件视图。

## 构建

需要 C 编译器、make、pkg-config、curl、tar、patch，
以及 libxcb、X11 协议头文件等 libX11 构建依赖。
缺少 xtrans 时，脚本把它下载并安装到私有构建目录。

从仓库根目录直接构建：

```sh
bash modules/xim-reconnect/build.sh "$PWD/build/xim-reconnect"
```

产物是 `build/xim-reconnect/libX11.so.6.4.0`。
`WECOM_XIM_BUILD_DIR` 可指定源码和中间产物目录，
默认是模块下的 `.build/`；`WECOM_BUILD_JOBS` 控制并行构建数量。
脚本核对下载包哈希，应用补丁后重新构建，不执行系统级安装。

也可使用统一管理入口：

```sh
./wecom-fix build xim-reconnect --prefix "$WECOM_PREFIX"
./wecom-fix install xim-reconnect --prefix "$WECOM_PREFIX" --dry-run
```

正常退出该专用前缀的企业微信及子进程后，再安装并通过统一入口启动：

```sh
./wecom-fix install xim-reconnect --prefix "$WECOM_PREFIX"
./wecom-fix doctor --prefix "$WECOM_PREFIX"
./wecom-fix launch --prefix "$WECOM_PREFIX"
```

## 隔离回归

测试依赖 Xvfb、libX11 开发文件、xcb-imdkit 和 xcb-util 开发文件。
测试程序创建两条 X11 连接，使用自建的
`wecom_reconnect_test` XIM 服务模拟输入法公告。
它不需要重启真实 Fcitx，也不需要操作企业微信。

```sh
python3 modules/xim-reconnect/run_tests.py \
  --library "$PWD/build/xim-reconnect/libX11.so.6.4.0" \
  --output /tmp/xim-reconnect-results.json
```

系统未安装 Xvfb 时，可通过 `--xvfb /path/to/Xvfb` 使用私有副本。
运行器固定使用 `:177`，不再自动选择显示号。启动前检查当前
`DISPLAY`、锁文件、文件系统 socket 和抽象 socket；
同号显示已被占用或无法核实时直接停止。启动后还核对锁文件 PID
属于本次 Xvfb 子进程。脚本不会清理现有 X11 socket 或锁文件。
两份 C 样本自身也拒绝任何非精确 `:177` 的 `DISPLAY`，
应始终由运行器启动。

2026-10-23 已完成原库 → 补丁库 → 原库的 15 次对照，
记录见 [test-results.json](test-results.json)。
表中“失败”是原库未达到正确行为，属于预期复现结果。

| 场景 | 原库 | 补丁库 | 再切回原库 |
| --- | --- | --- | --- |
| 仅处理第一个连接的事件，不应触发第二个连接回调 | 失败 | 通过 | 失败 |
| 仅处理第二个连接的事件，仍应收到其回调 | 失败 | 通过 | 失败 |
| 移除第一个连接回调后，第二个连接仍能被通知 | 失败 | 通过 | 失败 |
| 同一连接移除首个回调后，保留的回调仍能被通知 | 失败 | 通过 | 失败 |
| 回调执行过程中注销自身 | 通过 | 通过 | 通过 |

两个 C 测试程序使用 `-Wall -Wextra -Werror` 构建。
这些结果验证 libX11 回调路由和生命周期，不等于企业微信真实输入验收。

首次开发测试曾因 Xvfb 自动选择 `:0` 影响桌面 socket；
该次未执行回归用例，不能算作隔离测试。上述 15 次结果来自随后使用
独立 `:177` 的测试。运行器的启动前保护用于避免再次触碰现有显示服务。
新增保护经过 7 项模拟检查及语法检查；没有为验证保护而再次启动 Xvfb。

## 接入已有启动器

已有启动器的本地定制需要保留时，可使用
[install-existing.py](install-existing.py)。该工具只在唯一的
`set -eu` 后插入带明确标记的启动块；结构不匹配时停止。
以下路径需替换为本机实际启动器和私有产物目录：

```sh
launcher="$HOME/.local/bin/wecom"
fix_dir="$HOME/.local/share/wecom/xim-reconnect"

python3 modules/xim-reconnect/install-existing.py \
  --launcher "$launcher" \
  --directory "$fix_dir" \
  --library "$PWD/build/xim-reconnect/libX11.so.6.4.0"
```

安装器先验证原库哈希及 bwrap 可用性，保存带哈希的启动器备份，
再写入标记块。每次启动还会核对系统库和补丁库哈希。
检查失败时，`launch-existing.py` 输出原因并回到原启动器运行，
不会继续加载校验失败的补丁。

修改启动器不会替换现存进程中已经加载的库。
正常退出企业微信及该前缀子进程后，从刚配置的入口重新启动，
才能应用新的文件视图。

## 回滚

统一管理器安装的模块，在退出相关 Wine 进程后移除：

```sh
./wecom-fix remove xim-reconnect --prefix "$WECOM_PREFIX"
```

通过已有启动器安装的模块，移除同一启动器中的标记块：

```sh
python3 modules/xim-reconnect/install-existing.py \
  --launcher "$launcher" --directory "$fix_dir" --remove
```

`--remove` 只移除本模块标记块，保留启动器其他内容，并保存修改前副本。
它不需要当前系统库继续匹配旧哈希。私有产物和备份保留在指定目录。
随后退出并重新启动该前缀的应用，恢复使用系统库。
不要直接把早期启动器备份整体覆盖回去，以免丢掉后续其他功能修改。

## 验收边界

2026-10-23 已在 Wine 11.19 的实际企业微信聊天窗口验证：中文候选、
“测试”提交、英文和数字输入正常；Fcitx 重启并重新切到中文后，
企业微信保持同一进程而恢复候选和中文提交。测试文字均未发送并已清空。
新建的输入上下文会先处于英文状态，须在上下文建立后切回中文；
这与 XIM 连接丢失不同。详见[现场验收](live-validation-20261023.md)。

共享文档和其他既有修复未在本次重新做完整回归。

独立回归只模拟输入法服务出现和回调通知，不覆盖完整中文组合协议、
输入法引擎、候选位置或应用跨进程文字转交。
共享文档有候选但中文未提交的问题另见
[docs-ime](../../docs/features/docs-ime.md)。

## 来源与许可

- [官方 libX11 1.8.13 源码包][libx11-source]
  SHA-256：
  `69606f485c2c07c14ef64f75b7bb326d48587af33795d9ab3e607c0b5f94f11c`。
- [官方 xtrans 1.6.0 源码包][xtrans-source]
  SHA-256：
  `faafea166bf2451a173d9d593352940ec6404145c5d1da5c213423ce4d359e92`。
- libX11 的版权及许可保留于 [libX11 许可](../../licenses/libX11.txt)。

[libx11-source]: https://www.x.org/releases/individual/lib/libX11-1.8.13.tar.xz
[xtrans-source]: https://www.x.org/releases/individual/lib/xtrans-1.6.0.tar.xz
