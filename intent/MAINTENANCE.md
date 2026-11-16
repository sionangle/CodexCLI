# codex-intent 维护流程

只有 `release.json` 标记为 `verified-custom-release` 的完整交付才包含已验证维护工具。附带工具源码的 UI 候选仍须通过实际维护验收，不能作为第三阶段发行版。第一、二阶段便携包可独立运行。

## 版本与显示

上游初始基线为 `rust-v0.169.0` / `d32063f96c2dff1dc96af839b3952abb6acfeda4`。`baseline.json` 记录当前源码基线；`release.json` 记录已验证程序的 commit、上游版本、定制修订和 SHA256。源码中的 `release_verified: false` 表示源码本身不是发行证书，放行依据是交付报告。

`/config` → Sessions → Intent view 可保存显示模式。`Ctrl+T` 主动查看完整记录；`/subagents` 查看全部子任务与详情。面板不把计时或工具次数当作进度，不显示无可靠总量的百分比。

## 管理安装与隔离

在源码目录执行以下命令，`<交付目录>` 必须包含已验证的 `release.json` 和完整 package：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 install --release "<交付目录>"
```

默认管理安装位于 `%LOCALAPPDATA%\codex-intent`；可用 `-InstallRoot` 指定另一个短的专用目录。安装目录有私有访问权限，包含凭据和备份。不要使用官方 `.codex`、用户主目录、目录链接或过长路径。工具检查 Windows 后台服务 socket 的路径长度，不能通过静默禁用服务完成安装。

使用安装目录内 `codex-intent.cmd` 启动。只把此目录加入 PATH，勿加入内部 `package\bin`。管理启动器在整个前台进程生命周期持有共享锁；切换需要独占锁，客户端未关闭时会拒绝切换。后台任务还要经过服务空闲检查。

管理安装的数据是安装目录内 `d/<代号>` 的独立配置、登录和会话；`status` 显示当前配置文件的准确位置。初次使用单独登录，凭据存储使用 file。默认不共享官方配置、登录或会话，也不改官方安装及 PATH。项目级配置和机器级约束仍依照上游机制生效。便携版默认 `%USERPROFILE%\.codex-intent` 与管理安装也是两个数据目录；不要用绕过管理启动器的进程访问管理安装数据。

## 固定升级步骤

```powershell
# 查看源码基线、定制版本、当前安装及配置路径
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 status
# 查询官方最新稳定发布；网络错误不会当作“没有更新”
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 check
# 为明确选定的稳定 tag 建立独立 worktree 并逐个重放定制提交
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 adapt --tag rust-vX.Y.Z
```

适配报告保留在 `.intent-work/upgrades/<版本-时间>`。源码 worktree 位于主仓库同级的短目录 `iw-<代号>`，其准确位置写入报告的 `worktree`；这避免 Windows 格式工具的命令行长度限制。Git 操作单次开启长路径支持，不修改全局配置。报告记录目标 commit、原补丁和重放 commit。发生冲突立即停止，不改当前安装，不删除失败 worktree。到报告中的 worktree 解决冲突、保留原 commit SHA 在提交消息中并执行 `git cherry-pick --continue`，再运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 continue --adaptation "<适配报告目录>"
```

检查 `upstream-equivalence-candidates.log`，审阅目标上游相关源码，并在 `equivalence-review.json` 记录保留补丁的理由。若上游已实现等效功能，可提交移除对应定制实现的变更，记录 `replace-with-upstream` 及具体 `equivalence_evidence`；原重放提交仍须在历史中，意图视图、V2 面板和安全升级检查仍全部运行。不能通过 `--skip`、删除验收或放弃功能完成适配。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 validate --adaptation "<适配报告目录>" --previous-release "<上一已验证交付目录>"
```

验证使用目标 worktree 的 Rust 工具链、Cargo.lock 和上游二进制依赖锁。仅允许对齐上游锁中本地 workspace 包版本，外部依赖必须保持目标上游原样。linked worktree 共用主仓库的编译/下载缓存，仍按目标版本的锁逐项校验；配置、会话和报告独立。维护测试在主仓库同级的短 `mt-` / `ut-` / `ic-` 目录保存隔离安装、故障数据和故意冲突的 worktree，以满足 Windows socket 路径限制。完整构建、格式、核心断言、协议、配置、TUI、实际二进制/终端、升级与回滚验收必须通过。任何失败保留报告并停止发布，不切换当前版本。

安装目录的维护入口可执行 `status`、`switch`、`rollback` 和 `quiesce`。若从交付包或管理安装运行 `check`、`adapt`、`validate` 等源码维护操作，须用 `--repository "<定制源码仓库>"` 指明 Git 仓库。不要把已发布二进制目录当作源码仓库。

构建和自动化测试共享独占编译缓存锁，多个 worktree 的操作会依次执行。缓存记录源码路径、提交、工具链和锁文件；来源切换时，按当前 workspace 清单清理本地包生成产物，保留外部依赖缓存，避免复用另一 worktree 的本地库或同名 exe。相同干净源码的连续构建与测试继续复用缓存。清理前核对缓存真实绝对路径和本地包归属，版本与完整包检查仍必须通过。使用 PowerShell 构建入口，不直接调用内部 `build_package.py`。

## 空闲切换与回滚

关闭管理启动器中的前台客户端，并等待其任务完成。工具还会检查后台服务的真实线程状态和子进程；有运行任务、审批等待、后台命令、状态不明、远程控制开启或独立 updater 时拒绝切换。新的 `intent-quiesce` 只请求正常退出，超时不会强杀；重复请求不会变成强制中断。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 switch --release "<新验证交付目录>"
powershell -NoProfile -ExecutionPolicy Bypass -File .\intent\Maintain-Intent.ps1 rollback
```

切换先停止空闲服务，复制一致性完整数据快照并计算 SHA256，再在另一个数据目录运行候选 app-server 以执行正常迁移，检查 SQLite 完整性，最后原子更新选择文件。旧程序和快照保留。新数据目录保留配置和登录；服务包与 socket 等运行状态重新建立，服务从新 CLI 的完整包启动。安装不会只替换一个 exe。

回滚先按相同规则确认空闲，备份当前较新数据，再从升级前快照创建新数据目录，并同时选择旧程序。不会让旧程序读取较新格式。升级后新增的会话与设置保留在较新数据副本中，但不会自动合并进旧版本；恢复业务时可从 retained transaction 报告定位它们。项目源码文件的编辑不在 CODEX_HOME 数据回滚范围内。

`transactions/<时间>/report.json` 记录备份哈希、迁移检查和发布结果。状态文件是实际选择的依据：进程若在原子提交后、写最终报告前崩溃，先运行 `status` 核对当前版本，不凭一条报错猜测切换结果。失败的未发布数据副本留存供排查。

## 限制与前提

- 当前支持并验证 Windows x64；Python 3.13、Git、PowerShell 和 MSVC 构建工具为维护依赖。普通便携启动仅需 PowerShell。
- 上游 `dev-small` 是阶段构建 profile；可用 `validate --profile release` 跑完整性能构建门禁。
- 对已有 `intent.1/intent.2` 后台服务，工具不假设它具备非强制 drain；运行中会拒绝迁移。不要强制结束活跃任务来绕过门禁。
- 不支持外部程序绕过管理启动器并直接使用管理数据目录的并发访问。发现已有不明进程或状态时停止，而不是猜测可安全复制。
- 原始会话记录与工具结果保留；历史缺少任务正文、时间，或说明超出有界读取窗口时，面板明确显示缺失。不得把父线程 preview 作为子任务目的。
