# 验收记录

当前没有通过全部验收的发布版本。此文件将各要求关联到测试及实际记录；测试存在不等于测试通过。

环境审计发现早期进程继承了 `CODEX_SANDBOX_NETWORK_DISABLED`，上游部分网络测试会提前返回。这些套件的总通过数不能代表所有测试体实际执行，必须完整重跑。已增加入口拒绝检查，并要求在获准联网的独立进程中取消该继承标记；不改变测试内容或运行沙箱权限。上述标记不影响不含跳过条件的意图渲染单元断言，但最终仍以重新执行的整套报告为准。

## 第一阶段

| 场景 | 自动化覆盖 | 当前结果 |
|---|---|---|
| 运行中及完成后的长命令、万行输出不出现在意图视图 | `intent_hides_streamed_and_completed_command_without_discarding_source` | 通过（定向检查） |
| 动态工具 JSON、失败原始输出隐藏 | `intent_hides_dynamic_json_and_failure_output` | 通过（定向检查） |
| 显式详情及完整记录保留原文 | 上述测试、`intent_explicit_full_records_open_on_first_request` | 通过（定向检查） |
| diff、审批和错误可见 | `intent_preserves_changes_approval_and_actionable_error`、`intent_exec_approval_emits_proposed_command_and_decision_history` | 通过（定向检查） |
| 后台命令及交互信息隐藏 | `intent_background_interactions_retain_explicit_details` | 通过（定向检查） |
| 活动预览不重新显示原始信息 | `intent_passive_activity_preview_does_not_show_raw_live_output`、`agent_status_` 测试 | 通过（定向检查） |
| 布局宽度、历史重建和搜索一致 | `intent_retained_layout_survives_resize_disclosure_and_history_reconstruction`、`intent_search_keeps_collapsed_tool_sources_hidden` | 通过（定向检查） |
| 滚动历史中的活动单元先过滤再测量/绘制 | `intent_active_scrollback_filters_before_measurement_and_paint` | 通过（定向检查） |
| 显示开关保存并重新加载 | `intent_view_persists_and_reflows_when_switching_from_full_records` | 通过（定向检查） |
| 官方更新入口被阻止、重启菜单保留 | `intent_daemon_menu_blocks_updates_and_preserves_confirmed_restart` | 通过（定向检查） |
| 配置 schema 与生成器一致 | `Test-Intent.ps1` schema 生成及逐字比对 | 通过（定向检查） |
| 完整包、辅助程序、版本和隔离运行 | `Build-Intent.ps1` 候选构建及后续交互检查 | 未完成 |
| 真实终端调整尺寸、滚动、恢复会话 | 待终端验收 | 未完成 |

已完成的构建检查：上游 tag/commit 核对、外部依赖锁不变校验、Rust 格式检查、PowerShell 解析、Rust 1.95.0 安装、V8/ripgrep 固定资源校验、锁定依赖下载。`Prepare-Build.ps1` 已成功退出。

验证目录 `.intent-work/validation-*` 保留逐项 stdout/stderr 及 `report.json`。测试失败或测试数量不足均返回非零，不能发布。候选构建目前已实际检查“源码未提交”拒绝路径，并留下 failed 报告。

完整回归首次运行暴露出环境问题：Windows Terminal 的会话变量使图表快照出现不同颜色；官方发布 tag 的版本又不同于开发分支快照中的 `0.0.0`。已通过独立复现确认颜色差异，验证脚本固定终端能力，TUI 单元测试固定快照版本号。真实 CLI、执行服务和后台握手仍由 `smoke_package.py` 检查完整定制版本，不能以单元测试的固定版本替代。另有并发失败的嵌入式服务测试单独复现通过；完整 TUI 套件改为单线程执行，不跳过测试。

候选验收还要求报告开始和结束时源码均干净，且 Git commit 与候选包一致。开发中的未提交源码测试结果只用于诊断，不具备发布资格。

串行完整回归 `.intent-work/validation-20261116T140232564Z` 的结果为 6041 通过、8 失败、4 忽略（原有）。失败报告完整保留。当前修复将临时数据放入本项目短路径目录、让更新提示复用统一版本来源，并对三个共享测试辅助函数的四处内联快照声明允许重复执行。每次仍须匹配原有预期文本；没有删减断言或跳过测试。修复后的全套结果尚待产生。

短路径临时目录方案随后在配置类型检查暴露了项目发现污染，已撤回。最终保留 OS 临时目录，并只在启动快照中使用与界面相同的 `format_directory_display` 归一化 home 缩写。取消网络跳过标记后的定向检查已通过，完整回归仍须完成。

真实 ConPTY 诊断 `.intent-work/terminal-probe-20261116T143315966750Z/report.json` 通过 4 项检查：后台长命令运行中隐藏、完成及 resize/scroll 后隐藏、模型请求保留原始工具输出、显式完整记录显示原文。仅使用本机 HTTP 模拟响应与隔离配置；尚不覆盖恢复、实际 diff/审批交互及多子任务面板。临时测试工具与下载哈希记录保存在 `.intent-work/terminal-tools-install.json`。

## 第二、三阶段

V2 多任务面板、完成/失败/中断/重新派发/历史回放状态机尚未实现或验证。升级冲突保护、后台服务空闲检查、一致性备份、切换和真实回滚尚未实现或验证，不能用上述第一阶段测试替代。
