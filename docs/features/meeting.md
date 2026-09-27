# 内置会议加载依赖

[返回首页](../../README.md) · [安装说明](../usage/install.md)

## 解决的问题

企业微信 5.0.11.6018 的内置会议 3.26.511.637 曾因缺少 `SLWGA.dll`
无法加载会议平台；随后又遇到 Wine 的 `concrt140.dll` 未实现函数。
模块提供两个有先后顺序的兼容处理，不负责视频采集或会议 UI。

1. 自行编译最小的 32 位 `SLIsGenuineLocal` 兼容入口。
2. 使用微软 x86 VC++ 运行库，并仅为 `wwmapp.exe` 选择其并发库。

兼容入口校验空参数，正常参数返回 `E_NOTIMPL`。
它不返回授权成功，也不改变系统授权状态。

## 构建与安装

依赖：Python 3.11 以上、Wine、MinGW 的 i686 GCC。
先正常退出企业微信，再按需安装已核验的微软运行库。

```sh
python3 modules/meeting/install-vc-runtime.py ./vc_redist.x86.exe \
  --prefix "$WECOM_PREFIX"
```

默认仅显示计划，增加 `--apply` 才运行安装器。
该脚本只接受历史核验安装包的 SHA-256，不会下载或执行变化后的文件。
哈希只是版本识别；安装包仍须来自微软官方渠道。
官方链接可能提供不同版本的安装包；若哈希不匹配，安装脚本会停止。
已有可用的微软运行库时，无需重复安装。

随后构建兼容入口和注册表覆盖：

```sh
./wecom-fix build meeting --prefix "$WECOM_PREFIX"
./wecom-fix install meeting --prefix "$WECOM_PREFIX" --dry-run
./wecom-fix install meeting --prefix "$WECOM_PREFIX"
```

安装会在会议目录中新增缺失的 DLL。
若该位置已有文件，只允许替换已核验哈希的兼容库，并先备份原文件。
未知文件拒绝覆盖；卸载也会检查是否被后续修改。
本模块固定会议子目录版本，不扫描并猜测其他版本。

## 验证范围

企业微信 5.0.11.6018、内置会议 3.26.511.637 已有快速会议进入成功的
使用记录。摄像头、远端共享与 96 DPI 控件的验证范围见对应模块。

2026-09-28，在 Wine 11.18 的独立临时前缀中完成 32 位加载和导出测试：
空参数返回 `E_INVALIDARG`，正常参数返回 `E_NOTIMPL`，状态值为 `4`。
隔离测试不发起会议；本模块安装后的完整入会流程尚未完成端到端验证。

可以在安装 Wine 和 MinGW 的开发环境中重复隔离测试：

```sh
WECOM_TEST_NATIVE=1 python3 -m unittest discover \
  -s tests -p test_meeting.py -v
```

默认测试只检查安装器保护逻辑，不创建 Wine 前缀。
显式安装运行库前也会检查目标前缀没有活动 Wine 进程。
已有正常运行库时不必重复安装 VC++。

实际验收应进入测试会议，确认窗口打开、控件响应、退出正常。
单独运行 `wwmapp.exe` 没有正确会议参数，不构成入会成功。

## 回滚

```sh
./wecom-fix remove meeting --prefix "$WECOM_PREFIX"
```

恢复 `concrt140` 的原覆盖值，移除新兼容库或恢复旧兼容库备份。
微软安装器造成的运行库更新不属于该模块事务，需用 Wine 卸载器处理。
卸载前确认其他应用是否也依赖运行库；最好只在专用前缀操作。

## 官方来源

- [微软运行库下载说明][vc-docs]
- [微软 x86 安装器入口][vc-download]

[vc-docs]: https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist
[vc-download]: https://aka.ms/vs/17/release/vc_redist.x86.exe
