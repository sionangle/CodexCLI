# V2 子任务面板：实现前的数据核查

基线：`rust-v0.169.0` / `d32063f96c2dff1dc96af839b3952abb6acfeda4`。
此文件记录已核实的来源及限制，不代表面板已经实现。

| 数据 | 已核实的源码来源 | 限制和实现要求 |
|---|---|---|
| V2 路径和子线程 ID | `codex-rs/protocol/src/items.rs` 的 `SubAgentActivityItem`；`app-server-protocol/src/protocol/v2/item.rs` 的 `SubAgentActivity` | 保留路径作路由和详情，标题可回退到最后一段并替换下划线；不可虚构任务职责。 |
| 原始任务名和指令 | `core/src/tools/handlers/multi_agents_v2/spawn.rs` 的 `SpawnAgentArgs`、`ThreadSpawnRequest { task_name, message, ... }` | `SubAgentActivity` 本身不包含指令；需要读取对应子线程的原始任务消息，或者在聚合层保留明确来源的摘要。不能把旧 Collab spawn 的 prompt 当作 V2 已有字段。 |
| 活动时间戳 | `app-server-protocol/src/protocol/v2/item.rs` 的 `ThreadItem.started_at_ms/completed_at_ms`，以及 ItemStarted/ItemCompleted 通知 | 字段可能为 None；旧记录的完成时间 0 转成 None。不能用当前恢复时间冒充任务开始时间。活动条目的时间也不必然等于整个子任务的运行时间。 |
| 每次运行的时间和终态 | `app-server-protocol/src/protocol/v2/thread_data.rs::Turn` 的 `started_at/completed_at`（Unix 秒）、`duration_ms`、`status/error`；`turn.rs::TurnStatus` | 实际终态区分 Completed、Failed、Interrupted；InProgress 是唯一运行态。所有时间字段都可能缺失。须优先使用记录的 duration_ms 固定完成耗时，不能用父线程活动时间替代。 |
| 父子关系及历史预览 | `thread_data.rs::Thread` 的 `parent_thread_id`、`forked_from_id`、`source`、`preview`；`tui/src/app/agent_picker.rs` 使用 ancestor_thread_id 列出 V2 子树 | preview 通常是第一条用户消息，但 fork 历史可能包含父线程输入；尚不能直接将 preview 认定为子任务目的。列表元数据不等于完整 turn 历史。 |
| 实时进度 | `tui/src/app/agent_status_feed.rs` 的 `ThreadEventStore`、`ServerNotification::ItemStarted/ItemCompleted`；`AgentMessage` 和 `Plan` 的 text | 现有预览缓存是有界的，消息可能已不在缓存。需要补充线程历史订阅/分页；无内容显示“尚未收到进度”，不从工具调用数量生成百分比。 |
| 完成事件与结果 | `core/src/agent/control/completion.rs` 的 outcome/status；成功时发 `subagent-completed-{turn_id}` 活动，随后格式化跨 agent 结果消息 | 活动 payload 不含结果文本，也未为所有失败状态发同样的 Completed 活动。不能只监听父线程 Started/Completed；需结合子线程 turn 结果、状态和最终消息。 |
| 重新派发与普通消息 | `core/src/tools/handlers/multi_agents_v2/message_tool.rs` 的 `MessageDeliveryMode`；两者均发 `SubAgentActivityKind::Interacted` | `Interacted` 无法区分发送消息与启动新 turn。新任务必须由真实 turn ID/开始事件确认，不能把旧已完成任务直接改回运行中。 |
| 现有导航聚合 | `tui/src/app/agent_navigation.rs::record_sub_agent_activity`，`multi_agents.rs::sub_agent_activity_display` | 当前聚合偏向导航存活状态；没有用户要求的完整任务状态、每次派发耗时与进度记录。不能仅给旧版线程列表换标题。 |

下一阶段应以 `(agent_thread_id, turn_id)` 为运行实例键，保留路径层级作嵌套关系；对同一实例去重并保护终态。重放必须使用记录中的时间/顺序信息，缺失时明确标为未知。完成耗时应使用可靠的开始和结束时间并冻结；未记录的时间不得通过重启计时器补造。

尚需继续核实：子线程历史中任务消息的位置与 fork 边界、所有失败/中断通知的可订阅性、嵌套线程发现与恢复分页、数据在后台服务重启后的完整性。这些问题解决前不能宣称已满足 V2 面板验收。

## 任务正文的进一步核查

`thread_spawn.rs::spawn` 将任务作为 `AgentInput::Message`，并使用 `MessageDeliveryMode::TriggerTurn` 投递。`agent/control/delivery.rs` 把可读正文包装为 `NEW_TASK` 消息；普通 `send_message` 使用 `MESSAGE`，两者不能混为新的运行实例。

`session/mod.rs::record_inter_agent_communication` 将 `InterAgentCommunicationMetadata { trigger_turn }` 和 `ResponseItem::AgentMessage` 持久化，再发出原始响应条目事件。后者具有 author/recipient，内容可能为 `InputText`，也可能含 `EncryptedContent`。`event_mapping.rs::parse_turn_item` 不把这种 `ResponseItem::AgentMessage` 映射成常规可见 `TurnItem::AgentMessage`。因此仅依靠现有可见 turn items 或 thread.preview 无法保证取得任务正文。

第二阶段需要在原始事件/历史聚合入口核实订阅和恢复读取方式；有明文且归属明确时使用真实任务正文。加密正文无法从 TUI 自行恢复，必须明确显示缺失描述，不能从父线程的首条用户消息或内部路径推测职责。此处仅记录来源，尚未加入新的聚合或面板实现。
