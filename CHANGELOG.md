# Changelog

## Unreleased

- Keep the model dropdown opaque, including when repeatedly opening and closing it. Show the list immediately without Qt's temporary slide-animation window.

- Keep the recommended model list to the tested Qwen3 choices. Code-model adapters remain available for explicit experiments, with results and limitations documented separately; their small checkpoints were less useful on ordinary text.
- Keep short follow-up requirements focused on the stated problem rather than suggesting unrelated structural changes.

- Start completion without a fixed typing delay and suggest one short continuation at a time. Press Tab again to keep writing after the previous insertion is confirmed. Preserve the clipboard across consecutive accepts, finish partial words first, and retry punctuation-only output. Add a reproducible local benchmark for prompt quality and latency.

## 0.1.0b3 — 2026-09-26

- Add release PR automation, consistent version checks, and immutable draft releases with matching source and Windows packages.
- Fix completion not starting for external keyboard and Unicode input; accepting a suggestion no longer counts as new typing.

- 修复多行补全偶尔覆盖草稿的问题：更新内容前停止旧位置动画，并在布局尺寸更新完成后定位。
- 模型加载后保持常驻；空闲和暂停补全不再自动卸载，移除空闲释放设置。
- 托盘右键菜单增加“释放模型”，下次输入时重新加载；释放过程不阻塞界面，过期预热和生成请求不能重新加载已释放模型。
- README 和补全示意图改为英文，聚焦日常使用；首次配置提供可直接交给 Codex 的引导指令。

## 0.1.0b2

- 仅保留 Ollama 本地推理，移除云端 API 设置、调用代码和凭据依赖。

- 改为单次请求的自适应续写，统一提示词自然决定长短；粗略意见可补充具体要求，通常约 80–160 字。
- 保留短句补全；问候和模糊开头也会尝试猜测后续，合理建议由用户决定是否采用。
- 未写完的短语和疑问句优先续完原句，避免把“的”“应该”等句尾直接接成一段详细要求。
- 隔离历史里的生成规则；格式错误、空结果或复制内容最多修复一次，取消和超时覆盖整个请求。
- 保持“只分析、先不修改”等明确限制，旧结果不影响新草稿。
- 移除弹窗类型说明、设置中的补全方式和上下文方式；自动使用已确认对话，无法确认时直接续写草稿。
- 加宽较长建议的弹窗，完整显示待插入文字。
- 扩展两种后端的路由、取消、总超时、设置迁移，以及真实模型和原生插入回归。
- 重写中文 README，加入支持减少动态效果的补全示意动画；补充开发、更新、回退和发布验收指南。

## 0.1.0b1

首个 Windows x64 Beta 候选。

- 使用结构化续写保留草稿、数字、空格和未完成的词；历史对话作为引用背景。
- 支持短标题、冷启动输入框识别；自动上下文在任务未识别时使用草稿，并提供严格匹配选项。
- 取消过期请求，阻止导航或发送后的旧输出，完整显示插入内容，释放空闲模型。
- 修复隐藏脚本启动后的设置、下拉框与建议可见性，增加重复启动和真实模型桌面验收。
- 损坏配置保留备份并进入恢复流程；保存失败保留运行状态，下载和进程启动失败可以重试。
- 增加校验下载、每用户安装、升级回退、安全卸载、版本与源码追溯、校验和与许可证资源。
- 增加干净 Windows CI、草稿发布流程及隔离用户数据的自动化测试。

Beta 边界：VS Code 为实验支持；Windows 包尚未签名；宿主版本、输入法与硬件兼容范围按实际验收记录说明。更新采用版本包安装流程。补全由模型生成，是否采用和发送始终由用户决定。
