# 一条命令安装

从本仓库已发布的 GitHub Release 下载原生程序，安装后执行 `codex-intent`。无需克隆源码或安装 Rust、Git、Node.js、Python。当前安装脚本进入发布流程；仓库首次发布完成后，以下 latest 地址才可使用。

Linux x86_64（Ubuntu 22.04 或兼容环境）：

```bash
curl -fsSL https://github.com/sionangle/CodexCLI/releases/latest/download/install.sh | bash
```

Windows x64，在 PowerShell 中：

```powershell
irm https://github.com/sionangle/CodexCLI/releases/latest/download/install.ps1 | iex
```

安装成功后：

```text
codex-intent
codex-intent --version
codex-intent-maintain status
```

Linux 需要系统已有 Bash、curl、tar、gzip 和 coreutils。极简 Ubuntu 容器可先执行 `apt-get update && apt-get install -y ca-certificates curl`。以实际使用的账号安装；root 默认将命令放在 `/usr/local/bin`，普通用户默认 `~/.local/bin`。若原 PATH 没有该目录，安装器会更新用户 shell 配置；重新打开终端后生效，安装结果也显示可立即执行的完整路径。Windows 将命令放在用户 `~/.local/bin`，追加到用户 PATH，并更新当前 PowerShell 的 PATH。

程序和数据默认分别保存在 `%LOCALAPPDATA%\codex-intent` 或 `~/.local/share/codex-intent`。配置、登录、会话与官方 Codex 分离，首次运行需单独登录；`codex-intent-maintain status` 显示配置文件准确位置。安装仅注册 `codex-intent` 和 `codex-intent-maintain`，保留官方 `codex` 命令。已有便携版数据不会自动导入。

## 日常升级和回滚

```text
codex-intent-maintain status
codex-intent-maintain update
codex-intent-maintain rollback
```

`update` 下载本定制仓库最新的已验证预编译包，校验 SHA256 与完整包清单。有前台客户端、后台任务或不能确认的服务状态时拒绝切换。关闭 CLI、等待后台任务完成后重试即可；不会强制结束任务。重复安装最新版也可升级。

切换前一致性备份整个独立数据目录，为新程序创建新数据副本。回滚恢复上一程序和升级前的数据快照，保留新版期间产生的数据供手动恢复。旧程序不直接读取新版数据库；回滚后的维护入口继续使用保留的新版私有运行环境。

`check` 查询官方稳定源码版本；它不会直接安装官方二进制。维护者按 [MAINTENANCE.md](MAINTENANCE.md) 适配并验证新版本，再发布定制包。已发布目录应保持原样，不要往里面安装 Python 包。

## 自定义目录与容器

下载 `install.sh` 后可运行 `bash install.sh --install-root /short/private/path --bin-dir /your/bin`。Windows 下载 `install.ps1` 后可运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallRoot E:\Apps\codex-intent -BinDirectory E:\Apps\bin`。安装根目录必须专用、不能经过目录链接；过长路径会因后台服务 socket 限制被拒绝。`--no-path` / `-NoPath` 适用于自行管理 PATH 或隔离验证。

Docker 中应将安装根目录作为持久化卷保存（包括程序选择、私有数据和备份），并让实际用户可写。默认 Docker 策略可能禁止嵌套用户 namespace；这影响上游沙箱，与安装器无关。已观察到的限制和构建环境见 [LINUX.md](LINUX.md)，不应为了安装而设置 privileged。

目前仅支持 Windows x64、Linux x86_64；尚无 ARM64、macOS 包。程序暂用上游 dev-small 构建配置，未作 release 性能优化。

安装和普通启动不需要 Git；使用 Git 仓库与 worktree 功能时仍需兼容的 Git。Ubuntu 22.04 自带的 Git 2.34 不支持上游部分 worktree 参数；原生构建验收使用固定 Git 2.55.1。安装器不会替换已有 Git。

## 包来源与维护者发布

包内私有 Python 来自 Astral `python-build-standalone` 的固定发布 `20261110` / CPython `3.13.18`，目标与发布者 SHA256 记录在 [runtime-manifest.json](runtime-manifest.json)，原有许可证保留在 `runtime/`。它不加入 PATH、不影响系统 Python，仅运行随包维护代码。

维护者先完成 `release.py` 的全部构建、回归、真实终端及维护门禁，再用 `distribution.py --release <目录> --destination <新目录>` 打包。随后执行 `installer_probe.py --distribution <目录> --previous <上一平台已验证包>`。验收验证实际下载脚本、中文路径、无开发工具的命令启动、重复安装、损坏包拒绝、活跃客户端保护、数据回滚及私有运行环境保留。只发布 `passed-installer-acceptance` 且归档 SHA256 一致的包；失败证据保留，不覆盖原版本。
