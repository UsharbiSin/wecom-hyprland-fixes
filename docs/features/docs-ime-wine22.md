# Wine 11.22 的共享文档输入桥适配

[返回输入桥说明](docs-ime.md) · [Wine 11.21 记录](docs-ime-wine21.md)

2026-11-17 为 Arch `wine-11.22-1` 新增完整组件配置，保留
11.18/11.19/11.20/11.21 的原有哈希与企业微信宿主限制。
系统升级后，旧清单在系统 `imm32.dll` 处拒绝启动辅助程序，
共享文档中文输入桥因此未加载；此模块本身不阻止企业微信主界面启动。

## 源码与二进制依据

对比官方 `wine-11.21`、`wine-11.22` 标签中的十个依赖文件，
七个文件完全相同，三个文件仅有以下变化：

| 文件 | 变化与对本模块的影响 |
| --- | --- |
| `wow64win/user.c` | Raw Input 设备列表按返回数量复制，IME 参数转换不变 |
| `win32u/sysparams.c` | 新增 EGL 选项与 AMD 元数据，IME 候选坐标分发不变 |
| `win32u/input.c` | `ToUnicodeEx` 零长度结果清空首字符，IME 更新队列不变 |

`immdev.h`、`ntuser.h`、`win32u/message.c`、`win32u/imm.c`、
`winex11.drv/xim.c`、`imm32/ime.c`、`imm32/imm.c` 完全相同。
组合文字结构仍为 100 字节，i686 调用参数仍为 16 字节；
`NtUserImeDriverCall=0x0305`、`WINE_IME_TO_ASCII_EX=0`、
`SetIMECompositionRect=6` 未变。桥的 C 行为无需修改。

五个实际系统组件的 SHA-256 全部匹配 Arch 已安装包清单。
相比缓存的官方 11.21 包，`imm32.dll` 与 `win32u.dll` 的
`.text`、`.rdata`、`.data`、`.edata` 四个节全部相同。
`wow64win.dll` 的代码节变化，数据与导出节相同；其源码差异已审查。
不能据此将所有 11.22 构建视为相同，仍按完整包哈希匹配。

独立依据保存在 [源码证据][source-evidence]和[包与二进制证据][binary]。
源文件来自 [Wine 官方镜像的 11.22 标签][source]。

## 本次验证

- 十个官方源码文件通过对应版本的 `audit-source.py` 核验。
- 独立校验夹具使用 11.22 的两个前缀 DLL 和已验证企业微信宿主，
  与五个系统组件共同匹配 `wine-11.22-1`；不更新用户前缀。
- 桥 DLL、守护程序及原有空白窗口探针通过
  `-Wall -Wextra -Werror` 编译，产物不含新的桥接行为。
- 离线测试覆盖五个完整版本配置、所有有序版本对的组件混用拒绝，
  并固定旧四版配置的摘要，防止添加新版本时改写旧兼容范围。
- `python3 tools/check.py` 通过：129 项测试，5 项无关可选原生检查跳过。

```sh
python3 modules/docs-ime/audit-source.py \
  --wine-version 11.22 --source /path/to/wine-11.22
python3 -m unittest tests.test_docs_ime tests.test_docs_ime_profiles \
  tests.test_docs_ime_source -v
```

本次构建未启动真实企业微信，也未修改其前缀或已部署清单。
部署前应让该前缀通过正常 Wine 更新，确保系统和前缀 DLL 同属 11.22；
混合版本仍会被拒绝。真实共享文档的中文提交、候选定位、光标跟随，
以及返回聊天后的中英文输入，需要部署后单独验收。

## 后续版本自动识别方案

当前代码自动选择已核验的完整版本配置，不自动接受未知构建。
后续可按下列流程扩展升级检查；本次没有新增自动下载或信任写入逻辑。

1. 检测系统包版本和实际组件摘要变化，先报告受影响模块。
   系统升级与前缀升级应分开识别，不能把混合状态登记为新配置。
2. 自动验证包来源和包内文件摘要，拉取匹配版本的官方源码，
   对固定依赖文件生成差异报告；ABI 声明、调用编号、参数转换，
   以及 XIM 提交和候选定位路径分别检查。
3. 已核验源码和有效代码完全相同的重构建，可生成待验证候选配置；
   文件发生语义变化时须先审查，不能直接复制当前哈希到白名单。
4. 候选配置在独立前缀构建并运行现有测试，验证升级前后组合，
   继续拒绝混合版本、未知宿主和缺失组件。
5. 中文提交与候选位置经过实际界面检查后，原子发布整套配置，
   保留旧配置和回滚产物。报告分别记录静态、隔离运行与界面结果。

源码相同或探针能启动，仅能证明相应检查通过，不能替代共享文档验收。

[source]: https://github.com/wine-mirror/wine/tree/wine-11.22
[source-evidence]: ../../modules/docs-ime/abi-evidence-wine22.json
[binary]: ../../modules/docs-ime/binary-evidence-wine22.json
