# codex-intent 使用说明

普通使用按 [一条命令安装](INSTALL.md) 安装，然后输入 `codex-intent`。Linux 源码开发与 Docker 构建见 [LINUX.md](LINUX.md)。准确版本及验证范围以交付目录的 `release.json` 为准。不要将内部 `package/bin` 加入 PATH，该目录包含辅助服务需要的官方名称别名。源码升级和回滚流程见 [MAINTENANCE.md](MAINTENANCE.md)。

## 显示模式

运行定制版后，在 `/config` 的 Sessions 分组找到 **Intent view / 意图视图**：

- 打开：主对话隐藏命令、参数、脚本和工具原始输出；助手自己的说明和回答照常显示。
- 关闭：恢复上游详细显示方式。长输出的全部内容仍可在完整记录中查看。

开关会保存到定制版的配置中，立即触发重新绘制；切换至意图视图会关闭自动保留的详情状态。也可以编辑定制配置：

```toml
[tui]
intent_view = true
```

从 intent.5 起，在默认全屏界面直接点击工具状态后的 **[查看详情]**，打开该工具的原始命令和输出详情；滚轮或翻页键滚动，**Esc** 返回。运行中的详情会跟随同一次工具调用更新，关闭后保持意图视图和输入草稿。默认快捷键 **Ctrl+T** 仍可打开完整记录；快捷键可被用户的 keymap 覆盖。

点击需要程序接管的全屏界面：`[tui] fullscreen_transcript = true`（默认）。不要使用 `--no-alt-screen`，也不要关闭 alternate screen。在终端自己管理的滚动历史中，程序无法可靠识别历史文本的屏幕位置；该模式使用 Ctrl+T 查看完整记录。无需修改官方配置。

## 优先用补丁编辑并显示差异

`intent.6` 已保留补丁工具的原生 diff 显示。普通 shell/Python/PowerShell 写文件不会自动变成补丁事件；当前上游的回合 diff 跟踪器只接收 `apply_patch` 产生的变更。仅提高工具选择偏好不等于自动捕获任意命令的文件修改。

可以在 `codex-intent-maintain status` 指出的用户配置中添加以下**顶层**配置，放在 `[tui]` 等表之前。已有 `developer_instructions` 时，把规则追加到原字符串中，不要重复定义键或替换现有指令：

```toml
developer_instructions = '''
[Patch-first editing and visible changes]
For ordinary hand-written edits to text source files, use the dedicated apply_patch tool whenever it is available. This is the default editing workflow. Do not use Python, PowerShell, shell redirection, or whole-file rewrites merely for convenience, and do not invoke apply_patch through a shell when the dedicated tool is available.
If a patch fails because its context is stale, reread the relevant file and retry a corrected patch before choosing another editing method.
Shell commands remain appropriate for inspection, builds, tests, formatters, generated files, binary assets, and large mechanical transformations. Explicit user requests for another method take precedence over this preference.
When an alternative editing method is necessary, briefly state why. Afterwards inspect the actual changed files and present the relevant real diff in an assistant message so it remains visible in intent view; do not rely only on hidden command stdout. Clearly distinguish pre-existing user changes from your edits and never invent a diff or undo/reapply changes solely to manufacture a patch display.
When apply_patch already displays the diff, keep the accompanying explanation brief instead of repeating the same full patch.
'''
```

当前任务结束后重新打开 CLI，并新建会话，以确保补充指令进入新会话。恢复旧会话可能沿用已有的指令上下文。该设置增加开发者消息中的编辑偏好，不替换模型的基础指令；它依然是模型行为引导，不是禁止所有 shell 写入的执行拦截器。

在 Git 仓库中也可输入 `/diff` 打开差异页，按 Esc 返回。这里展示的是仓库差异，可能包含先前已有改动，不能直接当作本轮任务的改动清单。

配置字段见 [上游配置定义](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/config/src/config_toml.rs)，跟踪边界见 [上游回合 diff 跟踪器](https://github.com/openai/codex/blob/rust-v0.169.0/codex-rs/core/src/turn_diff_tracker.rs)。

## 子任务

主界面每个子任务只占一行：**任务名称 + 分:秒**；嵌套任务缩进显示。时间缺失时显示 `--:--`。点击有下划线的任务名称查看目的、当前工作、最近进度、状态、结果和内部路径，Esc 返回。没有明文说明或历史进度时只在详情中说明缺失，不把缺失提示用作主界面标题。

主助手本轮答复结束后，已经结束的子任务自动从面板收起，仍在执行的任务保留。这个时机依据主线程的完成事件；程序无法判断模型是否在语义上引用了每项结论。完整任务记录仍在 `/subagents` 中。同一 agent 重新接到任务时重新显示，并按新一轮计时。完成耗时固定；有可靠时间的历史回放也会收起已处理任务，缺少结束时间的旧记录会明确保留为未确认。

意图说明来自助手已经发送的进度消息；没有消息时显示“正在执行命令”等通用状态。程序不会根据命令猜测任务目的。审批仍显示作出决定所需的命令和信息；代码 diff 仍可见。执行失败保留失败状态及退出码，具体原因通过详情查看。

## 配置、登录与会话

一键安装使用安装根目录内 `d/<代号>` 的独立数据，运行 `codex-intent-maintain status` 查看配置路径；启动器会设置该目录。便携版默认使用 `~/.codex-intent`，官方版继续使用 `~/.codex`。直接绕过启动器执行内部二进制时，显式设置的 `CODEX_HOME` 仍会覆盖默认路径，不建议这样运行管理安装。

不自动共享或导入用户级配置、登录、会话数据库与后台服务。首次正式运行应在定制版单独登录；如需复用模型、终端等普通配置，只迁移已确认支持的设置，不复制后台服务状态或正在使用的数据库。

项目级 `.codex/config.toml`、项目指令和机器管理策略仍按上游规则读取；这是配置来源兼容，不是共享会话数据库。官方版不认识 `tui.intent_view`，请把该字段保存在定制版用户目录，不放入两版共同使用的项目配置或官方用户配置。

验证脚本为每次运行创建单独的临时 `CODEX_HOME`，不访问正式登录或会话。版本格式为 `codex-intent <上游版本>+intent.<修订号>`；准确交付版本和源码提交见交付包 `release.json`，也可运行 `codex-intent.cmd --version`。

## 构建入口

在项目根目录用普通 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Prepare-Build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Test-Intent.ps1
# 源码经过审查并提交，测试通过后才构建候选包。
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Build-Intent.ps1
```

默认构建使用上游 `dev-small` profile；测试和构建都支持 `-Profile release`。报告和候选产物位于 `.intent-work`。候选包不是已发布版本，仍需终端交互验收。

真实终端验收使用 Windows x64 / Python 3.13，以及固定版本与 SHA256 的 ConPTY 测试依赖：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Prepare-Validation.ps1
python .\intent\smoke_package.py <候选包的package目录>
python .\intent\validate_terminal.py <候选包的package目录> --stage 2
```

终端检查启动真实 CLI 和本机模拟模型，每次单独创建配置与会话。报告记录候选文件哈希和每个场景的原始日志；任何失败都会返回非零，不能作为升级发布依据。

不要只复制 `codex-intent.exe`：程序依赖完整包内的执行服务、代码宿主、Windows 沙箱辅助程序及资源。第三阶段提供保留完整包、协调后台服务并恢复一致数据快照的维护工具，使用方式和验收要求见 [MAINTENANCE.md](MAINTENANCE.md)。管理安装默认数据位于其安装目录的 `d/<代号>`，与便携版的 `%USERPROFILE%\.codex-intent` 分开；`Maintain-Intent.ps1 status` 显示准确配置位置。
