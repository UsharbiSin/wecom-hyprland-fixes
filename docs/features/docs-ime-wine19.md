# Wine 11.19 的文档输入桥适配

[返回功能说明](docs-ime.md) · [验证范围](../validation.md)

## 故障与适用范围

2026-10-05，Arch Wine 从 `11.18-1` 升级至 `11.19-1`。
原文档输入桥依赖 11.18 单版本哈希，7 项 Wine 系统及前缀组件变化后，
启动校验报 `imm32.dll` 不匹配，桥接器没有运行。
企业微信 `5.0.11.6018` 的 3 项宿主程序仍匹配，XWayland 与 CEF 修复仍有效。
因此，本次首先修复的是升级后门禁停用，不将其表述为已证实的 Wine 新回归。

本次新增 Arch `wine-11.19-1-x86_64` 构建，保留 `11.18-1`。
实际版本仍由 `module.json` 的 SHA-256 判定，不能仅凭 `wine --version` 放行。
其他发行版、重编译包或新版企业微信不能自动沿用此白名单。

## 私有接口核验

对比 Wine 上游 `wine-11.18` 与 `wine-11.19` 的 8 个源文件：

| 文件 | 对比结果及桥接依赖 |
| --- | --- |
| `include/immdev.h` | 文件相同，组合文字结构未变 |
| `include/ntuser.h` | 文件相同，驱动调用参数及调用编号未变 |
| `dlls/win32u/message.c` | 文件相同，消息转交未变 |
| `dlls/wow64win/user.c` | 文件相同，32 位参数转换未变 |
| `dlls/imm32/imm.c` | 文件相同，目标端公共 IMM 路径未变 |
| `dlls/win32u/imm.c` | 投递和按键处理改变，读取既有更新的路径未变 |
| `dlls/winex11.drv/xim.c` | 更新投递的参数表示改变，输入法布局判断调整 |
| `dlls/imm32/ime.c` | 输入法布局判断调整 |

桥接器不调用发生变化的 `WINE_IME_POST_UPDATE`，也不传入按键状态。
源端仍以 `WINE_IME_TO_ASCII_EX`、`state == NULL` 读取既有更新队列，
先查询长度，再在焦点和接收者检查通过后取出数据。
目标端继续使用公共 IMM 接口，不增加跨进程私有写入。

候选位置的 `SetIMECompositionRect` 调用编号和 32 位转换同样未变。
新增静态断言固定 i686 参数结构的 16 字节大小及两个指针字段偏移；
原有 100 字节组合结构和 32 位指针断言保留。
这些比较与断言只证明所用接口边界相符，不能替代实际输入验证。

## 整组哈希校验

7 个 Wine 目标各保存 `wine-11.18-1` 与 `wine-11.19-1` 的精确哈希，
3 个企业微信宿主仍使用固定哈希。校验器逐项求可匹配版本的交集，
只有全部 Wine 组件至少属于同一个完整配置，才允许继续。

这与逐个文件接受两个哈希不同：系统库属于 11.19、前缀 DLL 属于 11.18，
即使每个文件各自已知，也必须被拒绝。未知、缺失、空哈希或损坏配置同样拒绝。
`start.py` 与统一管理器 `check_targets` 使用相同规则，覆盖构建、安装和启动。
校验成功显示匹配配置，例如 `输入法桥组件校验通过：wine-11.19-1`。
`--stop` 不依赖当前组件仍匹配，因此升级后仍可停止旧辅助程序。

组件哈希从对应 Arch 缓存包及实际系统文件核对取得；
源码仓库仅保存校验清单，不提交 Wine、企业微信二进制或本机账号数据。

## 2026-10-10 验证记录

环境为 Arch Linux、Wine `11.19-1`、企业微信 `5.0.11.6018`，
Hyprland / XWayland、Fcitx5 `5.1.23` 配合 Rime。

原始清单的 `--verify-only` 返回 1；新清单在同一实际前缀校验通过，
匹配 `wine-11.19-1`。正式 32 位辅助程序和 DLL 编译通过并加载到已有会话。
本机启动器仅切换文档输入桥目录，保留 XWayland、菜单、Thunar 和剪贴板配置。
用户复测后确认共享文档输入已经恢复。

本次用户确认未细分正文、评论或表格控件，不能据此声称所有文档类型通过。
旧版完整候选跟随、聊天切换和热停止对照仍以 2026-09-28 记录为准；
本轮未独立重复完整界面对照，也未验证保存、协同、多屏或其他输入法。

新增 12 项双版本回归，覆盖完整旧/新配置、任一组件混用、交集为空、
未知或缺失文件、固定宿主校验、无效配置、空哈希、仅校验和升级后停止。
加上原模块 12 项回归，文档输入桥共 24 项通过。
整个仓库默认检查为 107 项，4 项原生检查默认跳过；显式启用后全部通过：

```sh
python3 tools/check.py
TMPDIR=/tmp WECOM_TEST_NATIVE=1 WECOM_TEST_COPYQ=1 python3 tools/check.py
```

原生检查只使用临时 Wine 前缀和独立 CopyQ 会话，不操作企业微信业务内容。
首次原生检查曾因临时目录过长导致 CopyQ 的本地套接字名称错误；
改用短临时目录后完整重跑通过，没有跳过或删除失败测试。

## 升级与回滚

使用最新版源码重新构建，并按[模块安装说明](docs-ime.md)安装或附加。
已驻留旧桥接 DLL 的会话须先完整退出，热停止不保证卸载已固定的 DLL。
不要通过覆盖系统库、移除哈希门禁或放宽窗口路径检查解决版本不匹配。

本机部署保留旧辅助程序和启动器备份。本模块仍支持 `--stop` 热停止，
但回滚到 11.18 专用旧清单后，Wine 11.19 会再次被拒绝，不能视为功能恢复。
通过管理器安装的模块可按原说明移除，其他模块与原始文件保持独立。

## 上游来源

以下链接固定到已核验的 Wine 标签；11.18 对照来源见原功能说明。

- [Wine 11.19 组合文字声明][immdev]与[内部调用定义][ntuser]。
- [更新队列及驱动调用][imm]、[XIM 投递][xim]与[WOW64 转换][wow64]。
- [消息转交][message]、[公共 IMM 实现][imm32]及[输入法布局判断][ime]。

[immdev]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/include/immdev.h
[ntuser]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/include/ntuser.h
[imm]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/win32u/imm.c
[xim]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/winex11.drv/xim.c
[wow64]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/wow64win/user.c
[message]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/win32u/message.c
[imm32]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/imm32/imm.c
[ime]:
  https://github.com/wine-mirror/wine/blob/wine-11.19/dlls/imm32/ime.c
