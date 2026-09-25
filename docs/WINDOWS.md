# Windows 使用指南

支持 Windows 10/11 x64。Codex Desktop 为主要宿主，VS Code 面板为实验支持。发布包包含应用；本地推理需要已安装的 Ollama 模型，也可选择 OpenAI-compatible 服务。

## 安装并启动

1. 从同一 Release 下载 Windows ZIP 和 `SHA256SUMS.txt`，核对 ZIP 的 SHA256：

   ```powershell
   Get-FileHash .\CodexCue-*-windows-x64.zip -Algorithm SHA256
   ```

2. 解压后进入 `CodexCue` 文件夹。便携使用可直接运行 `CodexCue.exe`，保留旁边的 `_internal` 目录。
3. 常规安装在该文件夹运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Start
   ```

   默认安装到 `%LOCALAPPDATA%\Programs\CodexCue`，无需管理员权限。指定位置可添加 `-InstallRoot 'D:\Apps\CodexCue'`。
4. 在设置中选择本地 Ollama 或云端服务，保存后打开 Codex 输入框开始使用。单击托盘图标可重新打开设置。

需要自动安装本地模型时，在源码仓库运行 `scripts/setup.ps1 -Start`。首次下载约 1.5 GB Ollama 和 2.5 GB 默认模型；代理与其他模型选项见仓库 README。

当前 Beta 的 Windows 包未签名。请从仓库的 Release 页面获取，SHA256 用于检查文件完整性。

## 日常设置

| 设置 | 用途 |
| --- | --- |
| 服务与模型 | 选择本地 Ollama，或配置云端地址、模型和密钥 |
| 模型空闲释放时间 | 默认 60 秒；0 表示每次请求后释放 |

续写长短由模型根据当前输入决定。识别到任务时自动参考近期对话，否则根据草稿生成。按 Tab 采用建议，继续输入会更新建议。空输入框保持安静，完整问句不会被续成答案。中文组词时 Tab 保留给输入法。暂停和退出也会请求释放本地模型。

云端配置填写 HTTPS 服务地址、模型名称与 API Key。草稿和相关对话片段会发送给该服务；密钥存储在 Windows 凭据管理器。本地 Ollama 请求仅连接本机回环地址。

## 更新与回退

1. 下载新版 ZIP 和校验文件，核对后解压到安装目录之外。
2. 从新版文件夹运行 `install.ps1 -Start`，使用原来的 `-InstallRoot`（如曾指定）。
3. 安装器校验每个文件，暂存新版，停止该安装目录的程序，再替换应用；目录切换失败会恢复旧版。
4. 启动后检查版本和设置，确认本地模型仍可用。保留上一个已验证的发布包。

需要回退时，将旧包解压到新目录，运行其中的安装器并指定同一安装位置。应用配置、凭据和 Ollama 模型保存在应用目录之外，升级与卸载都会保留。个人文件请存放在安装目录之外；维护脚本会拒绝删除不在发布清单中的文件。

便携用户可直接使用新解压的完整目录，保留旧目录便于回退。两个版本共用用户设置。发布更新采用以上手动流程。

## 卸载与保留数据

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\CodexCue\uninstall.ps1"
```

卸载保留以下数据，方便重装和其他应用继续使用：

- 设置与诊断日志：`%LOCALAPPDATA%\CodexCue`。
- 源码安装的后备配置、工具和模型：仓库的 `.local` 目录。
- API Key：Windows 凭据管理器中对应服务地址的 `codexcue` 条目；旧版可能使用 `codex-composer-companion`。
- Ollama 及模型库。

如需清除这些数据，先退出应用，再删除明确属于 CodexCue 的设置和日志目录、相应凭据。模型使用 `ollama rm MODEL:TAG` 按需移除。`CODEXCUE_DATA_DIR` 可为隔离部署指定独立配置与日志目录。

## 问题恢复

| 现象 | 处理方式 |
| --- | --- |
| 配置损坏 | 应用暂停补全并打开设置；可写目录中保留 `.corrupt-*.bak` 备份。保存设置后从托盘重新启用 |
| 下载失败 | 检查网络、代理和 Ollama 路径后重试；匹配的部分下载可以续传，损坏包会重新获取 |
| 没有建议 | 查看托盘的启用、模型和上下文状态；确认焦点位于 Codex 输入框 |
| 输入框识别失败 | 在 Codex 输入框按 `Ctrl+Alt+D`，通过剪贴板读取当前草稿 |
| 建议不合意 | 继续输入缩小范围；建议是否采用和发送由你决定 |

反馈问题时提供 `release.json` 的版本与提交、Windows/Codex 版本、模型名称及复现步骤。托盘可打开诊断日志目录；分享前只保留必要的状态与数字，避免附上真实对话、草稿、密钥或完整配置。

自动桌面验收的范围和兼容性边界见仓库的 `docs/RELEASE_READINESS.md`。
