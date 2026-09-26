# 开发指南

[返回首页](../README.md) · [发布标准](RELEASE_READINESS.md) · [发布流程](RELEASING.md)

## 准备环境

在 Windows 10/11 x64 的仓库根目录工作。先从托盘退出正在运行的 CodexCue，再启动源码实例；应用使用单实例机制。源码环境与日常发布包分开，开发时使用隔离数据目录。

```powershell
# 只准备应用和开发环境；已有 Ollama 时无需重复下载
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -EnvironmentOnly

# 启动开发实例，设置和日志写入独立目录
$env:CODEXCUE_DATA_DIR = "$PWD\.local\dev-data"
.\.venv\Scripts\python.exe -m codex_companion
```

首次运行时选择本机已安装的 Ollama 模型。测试已有安装包时先退出开发实例，避免两个进程争用同一输入框。终端结束后环境变量自然失效；同一终端可用 `Remove-Item Env:CODEXCUE_DATA_DIR` 清除。

## 一次改动的流程

1. 从最新 `main` 建立 `codex/<topic>` 分支，描述要改变的用户行为与复现条件。继续同一项工作时复用原分支；依赖未合并的 PR 时明确注明，不把尚未合并的代码当作主分支已有内容。
2. 修改相应模块。修复缺陷时先补能暴露问题的测试；提示词变化同时增加不同主题和干扰历史的真实模型用例。
3. 运行相关单元测试，再运行完整回归和 `python scripts/version.py check`。用户能感受到的变化，在 `CHANGELOG.md` 的 `Unreleased` 下写一句说明；日常开发不修改版本号。
4. 提交源码和合成测试，发起草稿 PR。审查重点是行为变化、失败恢复、隐私和证据，测试结果写进 PR。
5. 对提交后的候选包做桌面验收。验收通过后才替换日常运行的程序；发布按[发布流程](RELEASING.md)执行。

## 提交、PR 和回退约定

每个分支围绕一个需求，每次提交记录一组完整、可验证的改动，不把每次保存文件都做成提交。提交和 PR 标题使用英文，说明具体变化：

| 前缀 | 例子 |
| --- | --- |
| `fix:` | `fix: detect Unicode input before requesting completion` |
| `feat:` | `feat: add a tray action to release the model` |
| `docs:` / `test:` | 修改说明或测试 |
| `refactor:` / `chore:` | 整理代码、依赖或开发流程 |
| `feat!:` / `fix!:` | 需要用户调整使用方式的不兼容改动 |

推荐 squash 合并，PR 标题会成为主分支的提交说明；自动版本选择据此判断功能、修复和不兼容变化。Beta 阶段默认只递增 `b` 后的数字。工作流不会替你合并 PR。合并前确认 Windows 检查通过，PR 中写明测试范围和仍未验证的场景。

普通 PR 和主分支更新会运行 `Windows tests and build`，其 Windows 检查使用同一个可复用构建流程。机器人创建的发布 PR 会显式触发相同检查，不依赖 GitHub 默认禁用的机器人事件连锁触发。

出问题时通过 `git revert` 新增撤销提交，不重写共享历史。安装版本有问题时，用之前验证过的完整安装包回退，保留配置和模型。Git 标签和已经上传的发布文件不覆盖；修复后发布新版本。

分支保护建议要求 PR 和 `Windows checks / Test and package` 检查通过，再允许合并。保护规则属于 GitHub 仓库设置，需要主分支实际跑出该检查后配置；仅加入工作流文件不会自动开启保护。

```powershell
# 快速回归；临时文件留在仓库忽略目录中
.\.venv\Scripts\python.exe -m pytest --basetemp .local\pytest-dev

# 完整源码验收：安装维护、真实 UIA、剪贴板插入、窗口重启和本地模型
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -LiveModel qwen3:4b-instruct
```

`verify.ps1` 需要已解锁的 Windows 桌面，会短暂打开自己的合成测试窗口。它生成测试输入，使用隔离配置，不需要你手动打字。默认重复三轮；每次输出独立的 `.local/verify-*` 目录，失败以非零退出码结束。真实模型参数仅使用已安装模型。

## 模块与修改边界

| 模块 | 职责 | 修改后的重点验证 |
| --- | --- | --- |
| `sessions.py` | 读取允许的对话消息、识别任务 | 私有记录过滤、短标题、切换任务、重名歧义 |
| `windows_input.py` | 宿主编辑器、UIA、输入法、Tab 与剪贴板 | 焦点改变、输入法组词、粘贴回读、剪贴板恢复 |
| `state.py` / `app.py` | 请求生命周期、工作线程、弹窗和设置 | 旧请求失效、设置保存失败、完整文本显示 |
| `completion_prompt.py` / `model.py` | 统一续写提示词、输出校验和模型连接 | 单次请求、长短续写、模糊猜测、分析限制、重复内容、取消和总超时 |
| `config.py` / `startup.py` | 本地配置和启动恢复 | 损坏备份、隐藏启动、再次打开设置 |
| `scripts/` | 环境、打包、安装维护和发布 | 下载恢复、清单完整性、升级回退和数据保留 |

补全使用统一提示词，正常路径只有一次模型请求。格式损坏、空结果或复制原文时最多修复一次，共用总超时和取消信号。上下文自动使用已确认任务的近期对话，无法确认时只使用当前草稿；旧版模式设置在读取时忽略。过期结果不能复用到新草稿。

## 构建与本机更新

先提交代码，再构建到一个新的候选目录，保持正在使用的程序可用：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath .local\candidate\dist --workpath .local\candidate\build codexcue.spec

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1 -PackagedExe .local\candidate\dist\CodexCue\CodexCue.exe -LiveModel qwen3:4b-instruct
```

检查最终 `receipt.json` 的 `status`、提交号和 EXE 哈希。发布包通过[打包与完整性检查](RELEASING.md)后，用包内 `install.ps1 -Start` 升级；保留上一个已验证包作为回退来源。仅复制裸 EXE 会漏掉依赖和版本清单，应使用完整包。调整依赖时运行 `uv lock` 并提交锁文件。

## 日常维护

- **模型效果**：按[基准说明](BENCHMARK.md)运行 `scripts/benchmark_completion.py`，固定输入比较提示词与延迟，再审读是否续写、重复或编造要求。默认每次补一小段，Tab 插入经 UIA 确认后继续生成；没有固定键盘等待，但输入法、焦点和草稿确认仍须通过。
- **提示词迭代**：让 Codex 离线逐条阅读背景、草稿及新旧补全，按准确性、流畅性、上下文和有用程度评分，写清偏好与理由。针对一类失败修改提示词，再跑相同案例；选定版本后再看未参与调参的新案例。保存失败版本，效果变差就退回。标明是否为作者自评，不能把自动格式检查的 PASS 当作语义质量或真实用户采用率。完整步骤见[基准说明](BENCHMARK.md)。这项评审不进入实时补全流程。
- **运行指标**：`scripts/report_metrics.py` 统计等待时间、Tab 采用、请求失败和 UI 停顿。缺失样本记为未知，不能当作零错误。
- **文案和视觉**：README 聚焦用途、开始使用和关键选项；技术细节放入本文与发布文档。动画使用合成文字，并支持减少动态效果。
- **用户数据**：`.local/`、`.venv/`、`dist/`、日志、真实对话和本机配置只留在本机。回归用例使用新写的合成内容。

CI 负责干净 Windows 环境中的引导、回归、构建和包维护。交互桌面、真实模型及当前宿主兼容性由发布验收补齐，详见[验收矩阵](RELEASE_READINESS.md)。
