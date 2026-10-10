# 构建、安装与日常使用

[返回首页](../../README.md) · [使用说明目录](README.md)

## 1. 确认适用范围

本工具为 Windows 企业微信在 Arch Linux、Wine、Hyprland/XWayland
环境中提供分项兼容修复，需要已有企业微信安装及匹配的组件版本。

先准备已安装企业微信的专用前缀。不要直接把日常共享的 `~/.wine`
当成试验环境：DPI、DLL 覆盖和注册表关联会影响前缀内其他程序。
复制已有前缀前要正常退出全部进程，保留原副本，并自行处理登录数据。
工具只管理修复模块，企业微信前缀需自行准备。

```sh
wine --version
hyprctl version
export WECOM_PREFIX="$HOME/.local/share/wecom/prefix"
./wecom-fix list
```

验证环境为企业微信 `5.0.11.6018`、内置会议 `3.26.511.637`。
Wine 历史修复基线为 `11.17`，图片剪贴板支持 `11.18/11.19` 已核验构建。
共享文档中文输入模块支持 `11.18-1/11.19-1`，使用 Fcitx5 `5.1.23` 和 Rime。
`11.18-1` 有完整评论草稿对照，`11.19-1` 已由用户复测确认文档输入恢复；
本次范围见[升级核验](../features/docs-ime-wine19.md)。
精确限制以各模块哈希为准；同名版本包重新编译也可能不同。

## 2. 准备依赖

| 范围 | 构建或运行依赖 |
| --- | --- |
| 管理入口 | Python 3.11 以上、Wine、wineserver、bubblewrap |
| display | i686 MinGW GCC；Hyprland Lua 配置接口 |
| desktop | i686/x86_64 MinGW GCC、Python、Thunar、GIO |
| docs | Python、对应企业微信原版 CEF DLL |
| docs-ime | Python 3.11 以上、i686 MinGW GCC、Wine；运行需 fcitx5 |
| meeting | i686 MinGW GCC；按需准备微软官方 x86 VC++ 安装包 |
| camera | Wine 11.17 匹配库/头文件、官方源码、GCC、V4L2 |
| screencast | G++、pkg-config、libportal、PipeWire、X11 开发文件 |
| clipboard | Python、binutils、GCC/G++、x86_64 MinGW G++ |
| copyq | Python 3.11 以上、正在运行的 CopyQ；无需编译或 Wine 会话 |

原生桌面共享还需要工作的 PipeWire、xdg-desktop-portal 和对应桌面后端。
系统依赖需自行安装，无需为了使用其他模块降级整个系统的 Wine。
摄像头旧模块在 `11.18` 下预期拒绝构建，见[版本升级](upgrade.md)。

## 3. 只构建需要的模块

```sh
./wecom-fix build docs --prefix "$WECOM_PREFIX"
./wecom-fix build clipboard --prefix "$WECOM_PREFIX"
```

构建产物在仓库 `build/<模块>/`；模块之间互不覆盖。
输出产物哈希、适用前缀、应用版本和模块定义进入本地构建清单。
构建不会启用模块；安装后需要从统一入口启动才会加载修复。
`copyq` 桌面规则无需构建，通过 `install copyq` 单独启用，
不依赖 `launch`。详情见 [CopyQ 历史预览](../features/copyq.md)。
依赖不足或哈希不符会立即报错；不要删除检查来“继续安装”。

没有默认的“安装全部”命令：摄像头与剪贴板的已核验 Wine 版本不同，
办公默认程序还涉及个人偏好。一套环境应选择当前确实需要的模块。
共享文档输入需要单独构建 `docs-ime`；该模块每次构建和启动前，
还会校验系统 Wine、前缀 DLL 和相关程序，拒绝混用不同组件。

## 4. 查看计划并安装

```sh
./wecom-fix install docs --prefix "$WECOM_PREFIX" --dry-run
```

计划会显示安装位置、注册表操作和新增兼容库。
正常退出该前缀内全部企业微信、文档和会议进程，然后执行：

```sh
./wecom-fix install docs --prefix "$WECOM_PREFIX"
./wecom-fix install clipboard --prefix "$WECOM_PREFIX"
./wecom-fix doctor --prefix "$WECOM_PREFIX"
```

管理器保存原注册表值。原版 DLL 通过启动时只读绑定使用私有副本，
不会写回系统 Wine。会议入口模块是明确例外：它可能在缺失位置新增
`SLWGA.dll`，已知旧兼容库先备份，未知文件拒绝覆盖。

## 5. 从统一入口启动

```sh
./wecom-fix launch --prefix "$WECOM_PREFIX"
```

统一入口同时加载所有已安装且通过校验的模块。
入口仅对当前进程树使用 XWayland，并保留输入法所需环境变量。
全部 Wine 辅助进程和企业微信处于同一个文件视图；
宿主 Thunar 桥接器由会话管理器管理生命周期。

先完成目标模块文档中的真实界面验收，再决定是否将桌面快捷方式的
`Exec` 改为本仓库入口及绝对前缀路径。修改前备份原 `.desktop` 文件。
无需覆盖当前的 `wecom-hyprland` 启动器；保留它便于回退对照。

不要同时使用旧入口和新入口打开同一前缀。
已有外部 Wine 会话可能复用视图之外的 wineserver，故工具会拒绝混用。

### 已有会话中的文档中文输入

`docs-ime` 可在构建后附加到已有企业微信会话；这不启动第二份企业微信：

```sh
./wecom-fix build docs-ime --prefix "$WECOM_PREFIX"
python3 build/docs-ime/start.py --prefix "$WECOM_PREFIX"
```

另开终端，用相同命令追加 `--stop` 可热停止转交。
此方式只附加辅助程序，管理器的正式安装和移除仍要求先退出 Wine 会话。
替换桥接 DLL 后必须退出整个企业微信会话再启动，
仅停止并再次启动辅助程序不能保证卸载旧 DLL。
详细验收及限制见[文档中文输入](../features/docs-ime.md)。

## 6. 可选的桌面配置

以下操作与模块安装分开，按文档显式执行：

- [Hyprland 窗口规则和菜单点击](../features/display.md)。
- [Thunar/WPS 默认程序与 Wine 文件关联](../features/desktop.md)。
- [微软运行库安装](../features/meeting.md)。
- [停用旧剪贴板转换服务](../features/clipboard-legacy.md)。
- [CopyQ 企业微信图片历史预览](../features/copyq.md)。

这些操作有独立的影响范围和恢复方法，不要把它们等同于 DLL 模块回滚。

## 7. 安装文件与恢复数据

| 位置 | 内容 |
| --- | --- |
| 仓库 `modules/<模块>/` | 模块源码、构建脚本及配置 |
| 仓库 `build/<模块>/` | 当前构建产物与适用前缀清单 |
| XDG 数据目录 `wecom-fixes/<摘要>/modules/` | 安装副本与回滚状态 |
| XDG 数据目录 `wecom-fixes/<摘要>/` | 会话锁、启动描述及私有请求队列 |
| XDG 数据目录 `wecom-fixes/copyq/` | CopyQ 原生命令备份与专用规则状态 |

XDG 数据目录默认是 `~/.local/share`；摘要来自完整前缀路径。
安装状态目录只允许当前用户访问，保存卸载所需的原值和备份。
不要手动删除安装状态以跳过错误，否则可能丢失注册表和兼容库备份。

## 特殊安装路径

共享库通过 `LD_PRELOAD` 加载，该变量以空白或冒号分隔路径。
安装状态目录包含这些字符时，屏幕共享模块会明确拒绝安装或启动。
桌面模块的私有 `PATH` 同样不能使用含冒号的目录。
中文及普通前缀目录中的空格不受此限制；状态目录来自 `XDG_DATA_HOME`。
需要时将 XDG 数据目录设置为没有这些分隔符的独立路径，并保持启动时一致。
