# Wine 11.21 的共享文档输入桥适配

[返回输入桥说明](docs-ime.md) · [Wine 11.20 记录](docs-ime-wine20.md)

2026-11-02，Arch `wine-11.21-1` 已更新系统与企业微信专用前缀。
旧输入桥清单只允许 11.18/11.19/11.20，在 `imm32.dll` 校验处停止。
共享文档查找框可显示拼音候选，但候选偏离输入位置，选字后不上屏。
本次为已审查的 11.21 构建加入完整配置，保留全部旧配置与宿主限制。

## 源码和组件依据

对照 Wine 官方镜像的 `wine-11.20` 与 `wine-11.21` 标签，核对
组合文字结构、调用编号、WOW64 参数转换、XIM 更新队列、公共 IMM
提交和候选位置处理。桥的 C 行为无需改动：

| 依赖 | 11.20 到 11.21 的变化 |
| --- | --- |
| `immdev.h`、`wow64win/user.c` | 文件完全相同 |
| `win32u/imm.c`、`win32u/input.c` | 文件完全相同 |
| `winex11.drv/xim.c`、`imm32/ime.c`、`imm32/imm.c` | 文件完全相同 |
| `ntuser.h` | 仅新增三个 OpenGL swapchain 窗口消息 |
| `win32u/message.c` | 仅新增上述窗口消息的处理分支 |
| `win32u/sysparams.c` | 显示设备清理和日志变化，候选位置分发未变 |

新增消息属于另一组枚举，不改变输入桥使用的私有调用编号。
组合文字结构仍为 100 字节，i686 调用参数仍为 16 字节；
`NtUserImeDriverCall=0x0305`、`WINE_IME_TO_ASCII_EX=0`、
`SetIMECompositionRect` 编号 `6` 保持不变。
候选位置实现中的 per-monitor DPI 转换也未变化。

源码见 [Wine 11.21 官方镜像][source]。10 个文件的完整 SHA-256
及差异说明独立保存在 `modules/docs-ime/abi-evidence-wine21.json`；
11.19/11.20 的旧证据没有覆盖。

实际安装包的五个系统组件均匹配 Arch `wine-11.21-1` 包清单，
前缀中的两个 DLL 与系统原件一致，三个企业微信宿主固定哈希不变。
`module.json` 仍要求七个 Wine 组件共同匹配一个完整版本配置，
不允许逐项拼接不同版本，也不自动接受未知构建。

## 检查与构建

源码核验只用于审查，不会编辑兼容配置或启动 Wine：

```sh
python3 modules/docs-ime/audit-source.py \
  --wine-version 11.21 --source /path/to/wine-11.21
```

旧版核验可传入 `--wine-version 11.19` 或 `11.20`；
省略版本参数仍使用原有 11.20 证据。未知版本会被拒绝。
11.18 的二进制兼容配置保留，但本工具未收录其独立源码审查快照。

```sh
python3 -m unittest tests.test_docs_ime tests.test_docs_ime_profiles \
  tests.test_docs_ime_source -v
./wecom-fix build docs-ime --prefix /path/to/wecom-prefix
python3 build/docs-ime/start.py \
  --prefix /path/to/wecom-prefix --verify-only
```

离线测试覆盖四个完整版本配置、所有有序版本对的组件混用拒绝、
未知和缺失组件拒绝、旧配置哈希不变、宿主校验与失败后不启动 Wine。
桥 DLL、守护程序和探针使用 `-Wall -Wextra -Werror` 编译。

安装与切换入口前应正常退出企业微信，避免旧进程继续持有旧桥 DLL。
按[模块安装说明](docs-ime.md)操作；不要只修改部署目录中的哈希。

## 本机部署验收

在 Arch Wine 11.21-1、企业微信 5.0.11.6018、Hyprland 0.57、
Fcitx5/Rime 环境重新构建并安装输入桥，完整退出专用会话后重启。
运行中的聊天主进程和文档主宿主均加载新桥，组件校验报告 11.21。
再次完整退出、重启后，文档查找框的中文输入仍通过。

- 共享文档查找框：拼音候选紧贴输入光标，选字后“你好”正常上屏。
- 光标跟随：追加英文并移动到文本末尾后，候选随新的输入位置移动。
- 普通聊天：中文组合与提交、英文输入均通过，返回文档前清空草稿。
- 图片回归：同一会话 PNG/BMP 输入、企微内部回贴和原生 Wayland
  接收端解码均通过。

测试使用查找框和未发送草稿，未编辑共享文档、发送消息或评论。
首次观察出现短暂刷新延迟；等待界面稳定并重新测试后，候选与提交
均可见。此轮不据此声明所有启动时序与长时间运行均无问题。

## 验证边界

组件核验、编译与离线测试不能替代共享文档现场输入验收。
现场须分别验证中文候选位置、选字上屏、光标移动后的候选跟随，
以及返回普通聊天后的中文与英文输入；测试内容保持在可清除的草稿中。
保留旧配置表示仍接受原来核验过的完整组件组合，不表示本次重跑了
11.18/11.19/11.20 的全部真实界面测试。

[source]: https://github.com/wine-mirror/wine/tree/wine-11.21
