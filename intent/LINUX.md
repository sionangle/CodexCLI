# Linux 开发与运行

目标为 Ubuntu 22.04、x86_64，和 Windows 共用同一套 Rust 界面与数据聚合代码。Linux 使用原生 GNU 目标构建，保留完整包内的执行服务、代码宿主、ripgrep 和 bwrap。当前移植阶段以实际 `release.json` 和验收报告为准，源码存在不代表已完成 Linux 发布。

## 在已有 Ubuntu 22.04 容器内开发

用容器管理员安装构建依赖，随后以普通用户开发：

```sh
apt-get update
apt-get install -y ca-certificates curl git build-essential pkg-config libssl-dev libcap-dev clang llvm \
  cmake ninja-build protobuf-compiler python3 python3-pip python3-venv python3-tomli xz-utils unzip file
```

Python 3.10 使用发行版的 `python3-tomli` 读取 TOML；Python 3.11 及以上使用标准库。源码和编译缓存建议位于容器挂载的 Linux 文件系统或命名卷。构建前提交源码修改，候选包必须记录干净提交。

```sh
git clone --branch intent/stable https://github.com/sionangle/CodexCLI.git codex-intent
cd codex-intent
git remote add upstream https://github.com/openai/codex.git
bash intent/build-intent prepare
bash intent/build-intent prepare-validation
bash intent/build-intent build
bash intent/build-intent test
```

`prepare` 按目标源码的 `codex-rs/rust-toolchain.toml` 安装工具链，使用目标锁文件并校验 V8 官方校验值。工具链和缓存位于项目 `.intent-work/linux`，不会修改用户 PATH 或全局 Rust 默认版本。`prepare-validation` 安装带固定哈希的本地终端验收依赖。构建及测试共用排他文件锁，不能同时修改同一编译缓存。

`build` 输出完整候选包和报告，尚不代表可发布。继续运行二进制、终端验收，再经 `release.py` 汇集同一干净提交的证据：

```sh
PYTHONPATH="$PWD/.intent-work/terminal-tools" python3 -B intent/smoke_package.py '<build目录>/package'
PYTHONPATH="$PWD/.intent-work/terminal-tools" python3 -B intent/validate_terminal.py '<build目录>/package' --stage 2
```

终端测试使用本地模拟模型、真实 Linux PTY、隔离配置和会话，不需要登录。普通、后台、失败、恢复和 V2 子任务使用与 Windows 相同的界面断言。

## 独立 Docker 构建环境

仓库提供 `intent/Dockerfile.ubuntu22-build`，固定 Ubuntu 22.04 镜像摘要，预设普通 `codex` 用户；它是开发环境，不是预装已验收程序的运行镜像。

```sh
docker build -f intent/Dockerfile.ubuntu22-build -t codex-intent-build:ubuntu22 intent
docker volume create codex-intent-linux-build
docker run -dit --name codex-intent-linux-dev \
  --mount type=volume,src=codex-intent-linux-build,dst=/work \
  codex-intent-build:ubuntu22
docker exec -it codex-intent-linux-dev bash
```

随后在 `/work` 内克隆并按前一节构建。不要将 Windows worktree 的 `.git` 路径直接用作 Linux worktree；在 Linux 文件系统中克隆，保留完整 Git 祖先关系。

## 已验收包的运行与管理

便携包解压后运行 `./codex-intent`，默认数据为 `~/.codex-intent`，可通过 `CODEX_INTENT_HOME` 指定另一个独立目录。保留整个包。首次单独登录；不自动共享官方 `.codex` 的配置、认证或会话。

管理安装默认位于 `~/.local/share/codex-intent`，建议使用较短路径以满足 Unix socket 长度限制：

```sh
bash intent/maintain-intent install --release '<已验收交付目录>'
~/.local/share/codex-intent/codex-intent --version
~/.local/share/codex-intent/maintain-intent status
~/.local/share/codex-intent/maintain-intent switch --release '<新交付目录>'
~/.local/share/codex-intent/maintain-intent rollback
```

使用安装根目录的启动器，它在前台任务存续期间持有共享锁；维护操作需要排他锁。后台服务必须先确认没有活动 turn，再正常退出。切换会创建独立数据代次和一致性备份；回滚恢复升级前的数据，较新数据保留供人工恢复。不能绕过启动器直接启动内部二进制后仍假定维护锁能保护该会话。

检查和适配官方版本仍使用源码仓库：

```sh
bash intent/maintain-intent check
bash intent/maintain-intent adapt --tag rust-vX.Y.Z
# 查看适配报告并填写 equivalence-review.json，冲突必须先解决。
bash intent/maintain-intent validate --adaptation '<适配目录>' --previous-release '<上一 Linux 交付目录>'
```

验证失败保留报告，当前安装不切换。不能把 Windows 包切换到 Linux 安装，或反过来。Docker 的沙箱能力由服务器内核、容器配置和运行用户共同决定；当前本地模拟工具显示测试使用专用隔离测试配置，不等于已证明所有服务器沙箱配置都可用。正式工作保留正常审批和沙箱配置，按实际运行结果检查。
