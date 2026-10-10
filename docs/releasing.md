# 版本与发版

版本号使用 `vMAJOR.MINOR.PATCH`，当前自动流程只发布正式版本。
`0.x` 阶段仍保留每个模块的精确版本和哈希门禁。
缺陷修复增加 PATCH，新增模块或新增兼容能力增加 MINOR；
不兼容的安装或配置变更须明确记录，稳定版之后增加 MAJOR。

## 已建立的版本

- `v0.1.0`：新增 Wine 11.19 与 CopyQ 修复前的历史基线，
  对应提交 `74223165f1665430277acec902d4977512f98fd1`。
  图片剪贴板使用 Wine 11.18 组件。
- `v0.2.0`：图片剪贴板与文档输入桥增加 Wine 11.19 支持，
  并增加 CopyQ 历史缩略图。

Wine 版本按模块分别判断，例如摄像头补丁限定 11.17，
`docs-ime` 限定 11.18/11.19 已核验构建；版本号不代表整套修复的兼容范围。
准确范围见各版本源码内的功能文档及 `modules/<模块>/module.json`。

## 准备标签

1. 在功能分支完成修改、测试和文档，通过 PR 合并进 `main`。
2. 在 `CHANGELOG.md` 添加 `## v版本号 (YYYY-MM-DD)` 章节，
   写明变化、验证范围、升级限制，并在发版前随 PR 合并。
3. 确认目标提交的质量检查成功，再对 `origin/main` 创建签名注释标签。
   示例中的 `v0.2.0` 必须替换为尚未使用的目标版本。

```sh
git fetch origin main --tags
git switch --detach origin/main
python3 tools/check.py
python3 tools/release-notes.py --tag=v0.2.0 --output=/tmp/release-notes.md
git tag -s v0.2.0 origin/main -m 'v0.2.0'
git push origin v0.2.0
```

发布后不移动、不删除、不复用标签；需要修正代码时发布下一个版本。
不要一次推送所有本地标签。

### 本次维护者指定的 v0.2.0 重发例外

2026-10-10，维护者明确要求将文档输入修复纳入 `v0.2.0`，替换旧发布。
旧标签指向 `c7f46db7906602238ea856c95425e5e29a9aeed3`；
先保留其注释标签对象、目标提交和 Release 说明，再将本次修复经 PR 合入主线。
新标签必须指向通过检查的主线提交，并保持 GPG 签名和正常发布检查。
此例外不改变上述日常发版规则，也不能作为失败时反复移动标签的理由。

已有克隆不会自动覆盖同名标签。确认接受这次替换后，精确更新这个标签：

```sh
git fetch origin tag v0.2.0 --force
git verify-tag v0.2.0
```

也可重新获取该版本源码归档。不要强制更新其他标签或主分支。

## 自动发布

`.github/workflows/release.yml` 在推送 `v*` 标签后运行，检查规范版本号、
对应的非空变更章节、标签提交是否已进入 `main`，再执行离线质量检查。
格式错误、缺少说明或未合并的标签都会在发布前失败。

发布步骤再读取 GitHub 原生 Git ref/tag API，要求使用注释标签，
其签名状态必须为 `verified: true` 且 `reason: valid`。
标签名必须与触发版本一致，标签对象必须直接指向当前检出的 commit。
轻量标签、未签名、签名未验证或目标不一致时拒绝创建 Release。
`gh release create --verify-tag` 本身只确认远端标签存在，不验证签名。

GitHub 分支规则中的签名要求约束提交，不替代发版标签验证。
上述步骤只用于发布签名标签；不会增加 PR 提交签名检查或本地 GPG 验证。

工作流使用 GitHub 内置令牌和 `contents: write` 权限创建 Release，
无需额外 `RELEASE_TOKEN`。只使用固定提交的官方 checkout action。
没有依赖安装、应用打包或附件上传步骤；只提供 GitHub 的源码归档。
离线测试可能编译临时测试程序，不构建可分发的安装包。
已有同名 Release 时保留原内容；失败后在 Actions 中重跑原任务即可，
不要通过删除并重推标签触发重试。

发布说明先放入当前版本的变更章节，再附 GitHub 按 PR 标签生成的分类记录。
分类沿用其他项目的中文模板，并加入 `*` 兜底，避免遗漏未分类的 PR。
发布完成后核对版本、标签提交、说明和 ZIP / tar.gz 源码链接。

`v0.1.0` 是自动工作流加入前的历史提交，补建 Release 时由维护者明确
指定已有标签并提供历史版本说明；从包含工作流的 `v0.2.0` 起自动发布。

## 模板来源

- 中文分类：[tongyan_reports 的 release.yml][categories]。
- tag 触发与自动说明：[tongyan_reports 的发版工作流][workflow]。

[categories]:
  https://github.com/UsharbiSin/tongyan_reports/blob/main/.github/release.yml
[workflow]:
  https://github.com/UsharbiSin/tongyan_reports/tree/main/.github/workflows
