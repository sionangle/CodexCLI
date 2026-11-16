# codex-intent 使用说明

第一、二阶段便携包已经验证并交付，路径和验证范围见 [STATUS.md](STATUS.md)。运行第二阶段交付目录中的 `codex-intent.cmd`；不要将内部 `package/bin` 加入 PATH，该目录包含辅助服务需要的 `codex.exe` 别名。第三阶段维护工具正在验收，流程见 [MAINTENANCE.md](MAINTENANCE.md)。

## 显示模式

运行定制版后，在 `/config` 的 Sessions 分组找到 **Intent view / 意图视图**：

- 打开：主对话隐藏命令、参数、脚本和工具原始输出；助手自己的说明和回答照常显示。
- 关闭：恢复上游详细显示方式。长输出的全部内容仍可在完整记录中查看。

开关会保存到定制版的配置中，立即触发重新绘制；切换至意图视图会关闭自动保留的详情状态。也可以编辑定制配置：

```toml
[tui]
intent_view = true
```

默认快捷键 **Ctrl+T** 打开完整记录，按界面提示返回主对话。支持展开活动的界面也可以主动展开单个工具条目。这些主动查看操作可以显示完整原始信息，不会删除或改变模型收到的工具结果。快捷键可被用户的 keymap 覆盖。

意图说明来自助手已经发送的进度消息；没有消息时显示“正在执行命令”等通用状态。程序不会根据命令猜测任务目的。审批仍显示作出决定所需的命令和信息；代码 diff 仍可见。执行失败保留失败状态及退出码，具体原因通过详情查看。

## 配置、登录与会话

定制版默认使用 `%USERPROFILE%\.codex-intent`，本机通常为 `C:\Users\sion_\.codex-intent`。官方版继续使用 `%USERPROFILE%\.codex`。显式设置的 `CODEX_HOME` 仍会覆盖默认路径，运行前应确认它没有指向官方数据目录。

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
