# 贡献与合并规范

## Issue、分支与 PR

缺陷报告请提供复现步骤、版本及预期行为，功能建议请说明使用场景。
在功能分支上修改，通过 PR 关联对应 Issue。
一个 PR 聚焦一个可单独验证和回滚的问题，并同步相关测试及文档。

分支名示例：`fix/menu-input`、`feat/clipboard`、`docs/guide`。
提交采用 Conventional Commits，示例：

```text
fix(docs): 限定版本修正文档白屏
```

## GPG 签名

所有提交使用 GPG 签名，并在 GitHub 显示 `Verified`。
将公钥添加到 GitHub 账号，并验证提交使用的邮箱。

```sh
git config commit.gpgsign true
git config user.signingkey YOUR_GPG_KEY_ID
git commit -S -m 'fix(module): 描述用户可见行为'
git verify-commit HEAD
git log --show-signature -1
```

推送后，由 GitHub 原生 `required_signatures` 规则阻止未验证签名提交。
`main` ruleset 启用此要求；Actions 不再重复验证 GPG 签名。
本项目仍约定使用 GPG，原生规则本身接受 GitHub 支持的签名类型。

## 合并条件

- 通过「质量检查」及 GitHub 原生签名规则。
- 质量检查验证分支名、提交消息和 PR 标题，消息每行不超过 80 字符。
- 分支保持最新，全部审查讨论已解决。
- `main` 禁止强制推送和删除，保护规则同样适用于管理员。
- 使用 Merge commit，保留功能提交及其原有 GPG 签名。
- 禁用 Squash 和 Rebase 合并。
- 不启用线性历史要求，以允许 Merge commit。
- 合并后自动删除远端功能分支，关联 Issue 随 PR 关闭。

GitHub 创建的合并提交使用 GitHub 的 GPG 签名，功能提交使用作者签名。

## 检查与界面验证

```sh
python3 tools/check.py
```

修改兼容补丁时，请记录适用的 Wine、企业微信版本及组件哈希。
界面行为变化需按对应功能文档验证，并在 PR 中列出检查环境和结果。
构建、自动测试和实际界面验证分别记录。

## 上游与许可证

Wine 派生修改保留上游来源及许可说明。
新补丁请说明上游来源、适用版本与失效条件。
组件来源见[组件说明](docs/third-party.md)。

## 同步仓库规则

规则配置位于 `.github/branch-protection.json`。
具有管理权限的维护者可预览并同步：

```sh
python3 tools/apply-github-policy.py
python3 tools/apply-github-policy.py --apply
```

脚本配置必需检查、分支签名要求、管理员约束和 PR 合并方式。
必需检查来自 GitHub Actions；签名要求使用 GitHub 原生分支保护 API。
脚本保留现有 ruleset；维护者需确保其要求签名且不启用线性历史。

版本标签、兼容范围和源码发版见[版本与发版](docs/releasing.md)。
