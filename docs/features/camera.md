# 摄像头格式枚举崩溃

[返回总览](../../README.md) · [安装说明](../usage/install.md)

## 解决的问题

Wine `11.17` 的 WoW64 V4L2 后端在将媒体格式转换成 32 位结构时，
没有初始化 `AM_MEDIA_TYPE32.pUnk`。
内置会议枚举摄像头格式并释放该结构时，可能调用未初始化的 COM 指针，
表现为开启摄像头后会议崩溃。

本模块仅补上 `mt32->pUnk = 0`，从官方源码构建 Unix `qcap.so`。
Windows `qcap.dll` 保持原版。运行时通过 `bwrap` 私有映射后端，
不会替换系统 Wine 文件。

## 当前适用边界

已验证环境为 Wine `11.17-1`、企业微信 `5.0.11.6018`，
内置会议 `3.26.511.637`。

**2026-09-27 核验的 Arch Linux Wine `11.18-1` 不匹配此模块。**
其系统后端哈希与白名单不同，构建脚本会拒绝使用这些基础库。
该版本的摄像头表现尚未完成回归验证；
不能据此判断 Wine `11.18` 是否仍有原缺陷。

内部 Wine ABI 并不保证跨版本兼容。
不要删除哈希检查，也不要仅修改版本字符串来强行安装。

## 源码准备

依赖：Python 3、GCC、Wine `11.17` 开发头文件及已核验的基础库、
V4L2 开发环境与 `libv4l2.so.0`、bubblewrap。
开发头文件与基础库必须属于匹配的 Wine 环境；脚本不调整系统包版本。

从 [Wine 官方镜像][wine] 取得 `wine-11.17` 标签源码。
构建脚本不会自动联网下载，也不会修改这份上游源码。

```bash
git clone --depth 1 --branch wine-11.17 \
  https://github.com/wine-mirror/wine.git /path/to/wine-11.17
```

输入文件必须匹配：

| 文件 | 校验值位置 |
| --- | --- |
| `dlls/qcap/v4l.c` | 下方第一个 SHA-256 |
| `dlls/qcap/qcap_private.h` | 下方第二个 SHA-256 |

```text
1c9783660d5d29eaa7f92feabbd6deaccd8d153912e135e91a3903d93478d56a
e6335541ca0b28e3b13a4c7175e96cf7cbc2ee11b08a4e0fbcf10e6ee193b6b0
```

`prepare.py` 在独立构建目录保留上游版权声明、添加字段初始化，
并移除 Unix 后端不使用的 PE 专用 `wine/strmbase.h` 引用。
构建只包含 Unix 后端，链接同版本 Wine 的 `ntdll.so`。

## 构建与安装

```bash
prefix="$HOME/.local/share/wecom/prefix"
./wecom-fix build camera --prefix "$prefix" \
  --wine-source /path/to/wine-11.17
./wecom-fix install camera --prefix "$prefix"
./wecom-fix launch --prefix "$prefix"
```

构建和启动均受原系统组件约束。
`qcap.so` 的支持哈希为：

```text
9bc8291670e430ccefcb2688dd4477950555b1c4ae1dcc2cd3a8ba202fce1d41
```

`ntdll.so` 的支持哈希为：

```text
8a13d7042cef287802ea3bc60af40a7ee9def3130bb0d2be32c574693c9beb62
```

不受支持的系统仍可仅验证源码转换，不生成可安装后端：

```bash
python3 modules/camera/prepare.py \
  --source /path/to/wine-11.17 \
  --output /path/to/temporary-source
```

## 验证范围

Wine `11.17` 的原后端可稳定复现 `C0000005`。
修复后，两台摄像头的格式枚举、格式读取、能力查询与释放都通过；
这些探针未采集或保存摄像头画面。

2026-09-21，实际会议中的摄像头恢复可用，并经使用者确认。
验证只覆盖上述版本及两台设备，其他型号仍需单独验收。

源码准备流程已通过官方源文件哈希和补丁转换校验。
自动测试覆盖错误源码拒绝与输出隔离，不包含实时摄像头采集。
Wine `11.18-1` 因基础库不匹配而拒绝构建，未验证该版本的会议画面。

## 回滚与排障

```bash
./wecom-fix remove camera --prefix "$prefix"
```

退出并重启企业微信后，系统原后端恢复生效。
本模块不会修改摄像头权限、PipeWire 设置或系统 Wine 安装。

若升级 Wine 后模块被拒绝，先用新版本原组件复测，记录具体故障。
若设备完全不存在，另查硬件、`/dev/video*` 权限和驱动。
屏幕共享的窗口过滤警告属于另一个功能，不是此摄像头崩溃的证据。

## 来源与许可

[原版 v4l.c][v4l] 和 [qcap_private.h][header] 属于 Wine 项目，
以 LGPL-2.1-or-later 发布；生成源码保留原作者及许可声明。
分发生成库时须同时遵守 LGPL 对应源码等要求。
本仓库附有[许可证原文](../../licenses/LGPL-2.1-or-later.txt)。

[wine]: https://github.com/wine-mirror/wine/tree/wine-11.17
[v4l]: https://github.com/wine-mirror/wine/blob/wine-11.17/dlls/qcap/v4l.c
[header]:
  https://github.com/wine-mirror/wine/blob/wine-11.17/dlls/qcap/qcap_private.h
