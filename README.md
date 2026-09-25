<div align="center">

# CodexCue

**写个开头，接着往下。**

为 Codex 输入框补全下一句话，让粗略的修改意见变成清楚、可执行的要求。

[快速开始](#快速开始) · [安装与更新](docs/WINDOWS.md) · [开发指南](docs/DEVELOPING.md) · [更新记录](CHANGELOG.md)

![CodexCue 补全过程示意：输入粗略意见，预览续写，按 Tab 接到草稿后](docs/assets/completion-demo.svg)

Windows 10 / 11 x64 · Ollama 本地推理 · 中文 / English · Beta

</div>

## 顺着你的思路写

输入一点想法，停顿片刻，CodexCue 就会在输入框旁给出建议。按 **Tab** 接到草稿后；继续输入会更新建议，消息由你发送。

| 你写到哪里 | CodexCue 帮你补什么 |
| --- | --- |
| “我觉得……” | 猜一句自然的后续，也能借助当前对话找到方向 |
| “这个表格看起来有点挤” | 对齐、间距、换行和可读性方面的具体要求 |
| “登录步骤太绕了” | 操作步骤、交互路径和状态反馈的改善建议 |
| “导出的内容是否完整不容易判断” | 核对内容完整性、缺失项和失败场景的要求 |

模型在一次续写中自然决定长短：半句话接着写，粗略意见补充具体要求，通常约 **80–160 字**。合理猜测也会显示，由你决定是否采用。图中为合成文字演示，实际建议由所选模型生成。

## 快速开始

准备 Windows 10/11 x64、Codex Desktop，以及数 GB 可用空间。Codex 的 VS Code 面板处于实验支持阶段。

在仓库根目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Start
```

安装脚本会准备 Python 环境、Ollama 和默认模型 `qwen3:4b-instruct`，然后启动应用。首次下载约 **1.5 GB Ollama + 2.5 GB 模型**，无需管理员权限；下载中断后可重跑同一命令。

1. 打开 Codex 对话，在输入框写一点想法。
2. 停顿片刻，查看旁边的补全建议。
3. 按 **Tab** 采用，再编辑或发送。

单击系统托盘图标打开设置，右键可暂停补全、查看状态和日志位置。已有 Windows 发布包时，按[安装指南](docs/WINDOWS.md)安装或更新；草稿发布包仅对有仓库权限的用户可见。

<details>
<summary>使用代理、其他模型或云端服务</summary>

```powershell
# 为首次下载设置 HTTP 代理
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -ProxyUrl 'http://127.0.0.1:7890' -Start

# 选择其他 Ollama 模型
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -OllamaModel qwen3:1.7b -Start

# 使用 OpenAI-compatible 服务：先安装应用，再在设置中填入服务地址、模型和密钥
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -AppOnly -Start
```

设置支持选择已安装模型，也支持按名称下载。较小模型占用更少；补全质量和等待时间取决于模型与硬件。云端地址使用 HTTPS，API Key 存储在 Windows 凭据管理器。

</details>

## 贴合当前对话

- **上下文自动切换**：识别当前 Codex 任务，使用近期对话辅助续写；新任务可直接根据草稿补全。
- **输入优先**：继续打字、切换任务或发送消息后，旧建议立即失效；中文输入法组词时保留 Tab 给候选词。
- **本地优先**：默认通过本机 Ollama 推理，模型空闲后自动释放。选择云端服务时，草稿与相关对话片段会发送给该服务。
- **设置随系统语言**：中文 Windows 显示中文，其余显示英文。

诊断日志记录状态、耗时和数量，不记录草稿正文、对话正文或密钥。遇到识别问题，可在 Codex 输入框使用 **Ctrl+Alt+D**，通过剪贴板读取草稿并请求补全。

## 安装、更新与开发

| 你要做什么 | 从这里开始 |
| --- | --- |
| 安装发布包、升级、回退或卸载 | [Windows 使用指南](docs/WINDOWS.md) |
| 修改代码、验证效果、更新本机程序 | [开发指南](docs/DEVELOPING.md) |
| 查看发布门槛和验收覆盖 | [发布标准](docs/RELEASE_READINESS.md) |
| 制作可追溯的发布包 | [发布流程](docs/RELEASING.md) |
| 了解本次改动 | [Changelog](CHANGELOG.md) |

当前为 Windows Beta。发布包尚未进行代码签名；宿主版本和输入法兼容范围见[发布标准](docs/RELEASE_READINESS.md)。建议始终由你决定是否采用。

## License

[MIT](LICENSE) · [第三方组件说明](docs/THIRD_PARTY_NOTICES.md)

CodexCue 是独立项目，与 OpenAI 无隶属关系。补全使用你配置的 Ollama 或云端服务。
