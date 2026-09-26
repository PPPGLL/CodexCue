# 发布流程

[返回首页](../README.md) · [发布标准](RELEASE_READINESS.md) · [开发指南](DEVELOPING.md)

## 版本与候选

功能开发使用 `codex/<topic>` 分支，日常改动只更新 `Unreleased`，不用反复修改版本号。发布由三个 Actions 流程协作：日常检查、准备版本、生成草稿 Release。

### 准备下一个版本

1. 将准备发布的功能和修复合入 `main`，确认 `Unreleased` 的说明完整。
2. 在 GitHub 打开 **Actions → Prepare release → Run workflow**，分支选择 `main`，通常保留 `kind=auto`。
3. Action 自动新建草稿 PR，同时更新 `pyproject.toml`、`__version__`、`uv.lock`、更新说明和 `.github/release-plan.json`。它会显式请求 Windows 检查。
4. 审查版本与更新说明，待检查通过后将 PR 标为可审查并合入 `main`。
5. **Draft release** 自动测试和打包这次发布提交，核对文件哈希与来源，创建不可移动的版本标签和草稿 Release。Beta 版本另有预发布标记；稳定版也先保持草稿。

| `kind` | 行为 |
| --- | --- |
| `auto` | Beta 继续递增，例如 `0.1.0b2 → 0.1.0b3`；稳定版根据上次改版后的主分支提交标题判断 major / minor / patch |
| `beta` | 继续当前 Beta；稳定版则开始下一小版本的 Beta，例如 `0.1.0 → 0.2.0b1` |
| `stable` | 将当前 Beta 转为稳定版，例如 `0.1.0b3 → 0.1.0` |
| `patch` / `minor` / `major` | 稳定版明确升补丁、小版本或主版本；Beta 阶段请用 `beta` 或 `stable` |

自动识别以英文 Conventional Commit 标题为准：带 `!` 的不兼容变化提升主版本，`feat:` 提升小版本，其余提升补丁。PR 是最终审核点；描述不规范时显式选择 `kind`，不要让自动猜测替代审核。`uv.lock` 只更新本项目的版本，依赖保持锁定。

### 一次性启用与失败恢复

这些工作流必须先合入 `main`，手动入口才会出现在 Actions 中。仓库的 **Settings → Actions → General → Workflow permissions** 需要允许 **Allow GitHub Actions to create and approve pull requests**。各工作流仍使用各自声明的最小权限，不需要个人访问令牌。组织策略若禁止该选项，需要管理员处理。

重复运行 Prepare release 会复用同版本的未关闭 PR 并重新请求检查。它不会覆盖已有分支；如果推送成功而创建 PR 失败，修好权限后为该分支手动创建 PR，或检查并清理这个未合并分支后重试。发布 PR 的合并不要使用会抑制后续 Actions 的仓库 `GITHUB_TOKEN`；从 GitHub 界面正常合并即可。

Draft release 失败且尚未创建 Release 时，可以在 `main` 上手动重试。重试仍使用最近一次修改发布计划的确切提交，不会把后续开发代码塞进旧版本。已有标签只能指向原提交；已有 Release 和资源不会被覆盖。如果上传中断留下不完整草稿，应先检查草稿，再决定清理并重试或准备新版本。已公开的版本只通过后续版本修复。

本地可用 `python scripts/version.py prepare --kind auto --dry-run` 预览；它要求干净工作区，不修改文件，也不提交或推送。日常检查用 `python scripts/version.py check`。

发布物必须来自干净提交。构建中的 `_internal/build-info.json` 记录版本、提交和工作区状态；打包器会拒绝脏工作区或不匹配的构建。

## 本机制作候选包

1. 构建到新的目录，保留当前运行版本：

   ```powershell
   .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath .local\candidate\dist --workpath .local\candidate\build codexcue.spec
   ```

2. 在已解锁的 Windows 桌面验收这个确切的 EXE，使用已安装模型：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -PackagedExe .local\candidate\dist\CodexCue\CodexCue.exe -LiveModel qwen3:4b-instruct -Output .local\verify-candidate
   ```

3. 通过后打包，并传入刚生成的验收记录。输出目录必须是新目录：

   ```powershell
   .\.venv\Scripts\python.exe scripts\package_release.py --bundle .local\candidate\dist\CodexCue --output release-artifacts\candidate --verification .local\verify-candidate\receipt.json
   .\.venv\Scripts\python.exe scripts\check_release.py --assets release-artifacts\candidate --verification .local\verify-candidate\receipt.json
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\test_install.ps1 -Bundle release-artifacts\candidate\staging\CodexCue
   ```

4. 审阅真实模型样例，检查[发布门槛](RELEASE_READINESS.md)。本机详细日志留在 `.local/`；对外仅使用打包器生成的 `desktop-verification.json` 汇总。

目录 `candidate` 是命令示例。重试时换一个新目录，避免覆盖旧证据。

## CI 与草稿 Release

Windows CI 在干净环境中运行：环境引导、单元测试、下载恢复、安装维护、构建、依赖加载、许可证及对应源码收集、实际包的安装维护测试。

普通 PR、主分支推送和手动运行使用 `Windows tests and build`，测试包保留 14 天。正式准备候选使用上面的 Prepare release / Draft release 流程；不再使用旧的 `draft_release=true` 开关。发布流程下载同一次运行、确切发布提交的产物，验证后才上传到草稿。

CI 构建的二进制可能与本机包不同。下载 CI 生成的确切 ZIP，解压后重新执行桌面验收，再运行 `check_release.py`；旧 EXE 的通过记录不能用于新 EXE。发布前补充与最终二进制匹配的公开验收摘要。

## 发布文件

| 文件 | 内容 |
| --- | --- |
| `CodexCue-VERSION-windows-x64.zip` | 应用、安装器、卸载器、使用说明、许可证和 `release.json` |
| `CodexCue-VERSION-source.zip` | 对应提交的项目源码 |
| `sources/*.tar.gz` | 精确对应 Qt Base / PySide 版本的上游源码 |
| `SHA256SUMS.txt` | 发布资源校验和 |
| `desktop-verification.json` | 与 EXE 哈希关联的公开验收摘要 |

Qt 动态库保持可替换。依赖变化后更新许可证与源码清单，分发时一并提供对应源码。项目源码使用 `git archive` 导出，发布输入中不包含 `.local/`、环境、会话、配置或凭据。

## 最后检查与发布

确认版本、提交、资源清单、哈希、CI、桌面验收和更新记录一致，无未解决的 P0/P1 缺陷。发布说明写明测试过的宿主/Windows/模型、主要变化、升级方式与 Beta 边界。任何文件改变都要重新验证受影响的证据。

经明确决定后再发布现有草稿。合并主分支、公开 Release 和将仓库改为公开分别处理。当前 Beta 二进制未签名；后续签名需要单独配置证书。
