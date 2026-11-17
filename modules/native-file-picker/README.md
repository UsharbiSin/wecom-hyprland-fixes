# 企业微信 GTK 文件选择桥接

点击企业微信的文件选择入口后，显示系统 GTK 文件选择窗口。
选择结果通过原窗口交回 Wine/企业微信，保留原应用的附件校验与回调。
GTK 窗口中的“使用原窗口”可以对当前请求恢复原选择器。

## 适用范围

- 仅企业微信专用前缀内的 `WXWork.exe`、`WXWorkWeb.exe`。
- Wine 11.22 的已验证 `comdlg32.dll`；启动时校验两个架构的哈希。
- Win32 经典打开框与 `IFileOpenDialog`；保存框与纯文件夹框不接管。
- 中文、空格、单选及同目录多选；文件类型筛选不区分大小写。
- 带额外自定义控件、不兼容布局或过长路径会保留或恢复原窗口。
- 歧义路径会在 GTK 中提示，可重新选择或点击“使用原窗口”。

Thunar 是文件管理器，不提供这里需要的 Win32 文件选择返回协议。
本模块使用 GTK 3，可使用系统主题、目录侧栏和书签。
不修改 Wine DLL、企业微信程序文件或其他 Wine 前缀。

## 构建与现有启动器安装

依赖 Python 3、PyGObject、GTK 3、Wine、Bubblewrap 和 MinGW x64 GCC。
还需已有企业微信的 `wine-runtime-compat` 已验证运行时配置；
以下安装命令不适用于任意 Wine 前缀。
运行环境来源与边界见[已有启动器运行环境说明][runtime]。

```sh
bash modules/native-file-picker/build.sh
python3 modules/native-file-picker/install-existing.py \
  --launcher /absolute/path/to/wecom-launcher \
  --prefix /absolute/path/to/wecom-prefix \
  --destination /absolute/path/to/native-picker \
  --build build/native-file-picker
```

安装器创建启动器备份，只添加自己的标记区块。
以后从此启动器打开企业微信时，辅助程序自动启动，不需单独执行命令。
这不是桌面登录自启动；直接绕过该启动器运行 `WXWork.exe` 不会自动加载。
已启动的企业微信可独立运行安装目录中的 `bridge.py --prefix ...`，
无需重启应用。同一前缀的文件锁防止重复实例。

恢复默认行为时，先结束本模块的 `bridge.py` 进程，
再从启动器删除 `BEGIN/END WECOM NATIVE FILE PICKER` 区块。
不要覆盖启动器的其他新改动。

## 故障恢复与边界

原窗口在 GTK 窗口显示后才隐藏；关闭桥接进程或管道断开会恢复原窗口。
原应用拒绝所选文件、控件回填失败或布局变化时，恢复原窗口继续处理。
企业微信全部退出后辅助进程自动结束。
提交前重新核对对象、窗口、原接口选项及结果缓冲区，避免使用过期数据。
生产日志仅记录事件类型，不记录选择的文件路径。

当前只保证选择器接管与路径返回，不以自动测试代替实际附件发送验收。
实机检查仅打开与取消选择器，不发送消息、不上传附件、不提交审批。

## 验证

2026-11-17 在企业微信 5.0.11.6018、Wine 11.22、Hyprland 0.57
中实测聊天发送文件和审批添加附件的 GTK 打开、取消及返回界面。
另在实际运行时视图中验证启动校验与 GTK 初始化，包括继承现有输入法环境。
未退出生产应用重测完整冷启动，也未发送或上传实际附件。

```sh
python3 tools/check.py
python3 -m unittest tests.test_native_file_picker -v
```

`tests/native-file-picker-probe.c` 为无网络的独立 API 验证器，
可编译为 x86/x64，覆盖 `classic-w`、`classic-a`、`com`，
以及 `--multi`、`--hook`、`--hook-veto`。
自动选择参数只允许用于独立测试前缀和精确匹配的测试进程。

[runtime]: ../../docs/usage/runtime-recovery.md
