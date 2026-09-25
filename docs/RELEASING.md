# 发布流程

[返回首页](../README.md) · [发布标准](RELEASE_READINESS.md) · [开发指南](DEVELOPING.md)

## 版本与候选

功能开发使用 `codex/<topic>` 分支。准备发布时确定唯一版本，同时更新 `pyproject.toml`、`src/codex_companion/__init__.py` 和 `CHANGELOG.md`，运行 `uv lock` 后提交。使用新的版本号制作后续候选，已验证的旧版标签和文件保持不变。

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

推送候选分支，待相同提交的 CI 成功后审查草稿 PR。在 Windows workflow 中手动选择 `draft_release=true`，可以创建草稿预发布。流程保持仓库可见性，创建的 Release 始终为草稿。

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
