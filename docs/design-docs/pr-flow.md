# PR Flow

English TL;DR: Treat this fork as the primary project repository, but land all non-trivial changes through feature branches and pull requests into `main`.

Updated: 2026-05-06

Key terms: pull request, fork, origin, upstream, branch protection, feature branch

## Purpose

本项目虽然来自 DuckDB fork，但当前仓库 `0AyanamiRei/duckdb` 是 ADL-OPT 的主项目仓库。为了让研究过程、实验 harness、文档和后续代码修改都有清晰审查记录，默认不直接向 `main` 推送工作提交，而是通过 feature branch 和 pull request 合入。

## Remotes

本地 remote 约定：

```bash
origin   https://github.com/0AyanamiRei/duckdb.git
upstream https://github.com/duckdb/duckdb.git
```

`origin` 是 ADL-OPT 主仓库。`upstream` 只用于同步 DuckDB 官方更新。

## Normal Change Flow

每次开始新工作前，从 `origin/main` 开分支：

```bash
git checkout main
git pull --ff-only origin main
git checkout -b adl-opt/<short-task-name>
```

完成修改后提交并推送分支：

```bash
git add <files>
git commit -m "<concise change summary>"
git push -u origin adl-opt/<short-task-name>
```

然后创建 PR：

```bash
gh pr create \
  --repo 0AyanamiRei/duckdb \
  --base main \
  --head adl-opt/<short-task-name> \
  --title "<PR title>" \
  --body "<summary, validation, risks>"
```

如果不用 GitHub CLI，在 GitHub 网页创建 PR 时必须确认：

- base repository: `0AyanamiRei/duckdb`
- base branch: `main`
- compare branch: 当前 feature branch

不要把 ADL-OPT 工作误提交成发往 `duckdb/duckdb` 官方仓库的 PR。

## Empty PR Validation

空 PR 或近似空 PR 可以用于验证分支保护、权限、GitHub Actions 和审查流程。验证 PR 应该只包含轻量文档或元数据修改，避免污染实验代码。

推荐分支名：

```bash
docs/pr-flow-validation
```

推荐 PR 标题：

```text
Validate ADL-OPT PR flow
```

验证完成后，PR 可以正常 merge，也可以关闭；如果 merge，应保留 commit 作为流程记录。

## Upstream Sync Flow

同步 DuckDB 官方更新也通过 PR：

```bash
git fetch upstream
git checkout main
git pull --ff-only origin main
git checkout -b sync/upstream-YYYY-MM-DD
git merge upstream/main
git push -u origin sync/upstream-YYYY-MM-DD
```

然后创建从 `sync/upstream-YYYY-MM-DD` 到 `main` 的 PR。同步 PR 应单独存在，不要混入 ADL-OPT 功能修改。

## Branch Protection

建议在 GitHub 仓库设置 `main` 分支保护：

- Require a pull request before merging.
- Require status checks to pass before merging once CI exists.
- Do not allow bypassing the above settings.
- Restrict force pushes.
- Restrict deletions.

这样可以保证 ADL-OPT 的项目历史始终能追溯到 PR。

## Agent Rules

Agent 执行非平凡修改时应：

- 先确认当前分支。
- 从 `main` 新建语义化 feature branch。
- 不直接提交到 `main`，除非用户明确要求。
- 提交前运行与修改范围匹配的轻量验证。
- 在最终回复中给出 branch、commit、验证命令和 PR 创建状态。
