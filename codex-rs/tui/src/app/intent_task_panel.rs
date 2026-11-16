//! Bounded background hydration for the persistent V2 task panel.
//! Requests never block input or the live notification path.

use super::*;
use crate::intent_tasks::IntentTasks;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::SortDirection;
use codex_app_server_protocol::Thread;
use codex_app_server_protocol::ThreadItem;
use codex_app_server_protocol::ThreadItemOutputDeferral;
use codex_app_server_protocol::ThreadItemsListCursor;
use codex_app_server_protocol::ThreadItemsListParams;
use codex_app_server_protocol::ThreadItemsListResponse;
use codex_app_server_protocol::ThreadReadParams;
use codex_app_server_protocol::ThreadReadResponse;
use codex_app_server_protocol::ThreadTurnsListParams;
use codex_app_server_protocol::ThreadTurnsListResponse;
use codex_app_server_protocol::Turn;
use codex_app_server_protocol::TurnItemsView;
use futures::StreamExt;

#[derive(Default)]
pub(super) struct IntentTaskPanelState {
    pub(super) tasks: IntentTasks,
    pub(super) root: Option<ThreadId>,
    pub(super) request: Option<Uuid>,
    last_refresh: Option<Instant>,
    hydrated: HashSet<ThreadId>,
}

#[derive(Debug)]
pub(crate) struct TaskSnapshot {
    pub(super) thread: Thread,
    pub(super) turn: Option<Turn>,
    pub(super) activities: Vec<ThreadItem>,
}

async fn load_snapshot(
    handle: AppServerRequestHandle,
    thread_id: ThreadId,
    read_dispatches: bool,
) -> Result<TaskSnapshot, String> {
    let response = handle
        .request_typed::<ThreadReadResponse>(ClientRequest::ThreadRead {
            request_id: RequestId::String(Uuid::new_v4().to_string()),
            params: ThreadReadParams {
                thread_id: thread_id.to_string(),
                include_turns: false,
            },
        })
        .await
        .map_err(|e| e.to_string())?;
    let turns = handle
        .request_typed::<ThreadTurnsListResponse>(ClientRequest::ThreadTurnsList {
            request_id: RequestId::String(Uuid::new_v4().to_string()),
            params: ThreadTurnsListParams {
                thread_id: thread_id.to_string(),
                cursor: None,
                limit: Some(1),
                sort_direction: Some(SortDirection::Desc),
                items_view: Some(TurnItemsView::Summary),
            },
        })
        .await
        .map_err(|e| e.to_string())?;
    let mut turn = turns.data.into_iter().next();
    let mut activities = Vec::new();
    let mut cursor = None;
    let mut seen = HashSet::new();
    // The summary includes the final assistant response, but commentary can precede hundreds of
    // tool items. Scan a bounded window, keeping only authored text and V2 dispatch metadata.
    for _ in 0..4 {
        let page = handle
            .request_typed::<ThreadItemsListResponse>(ClientRequest::ThreadItemsList {
                request_id: RequestId::String(Uuid::new_v4().to_string()),
                params: ThreadItemsListParams {
                    thread_id: thread_id.to_string(),
                    turn_id: if read_dispatches {
                        None
                    } else {
                        turn.as_ref().map(|t| t.id.clone())
                    },
                    cursor: cursor.clone().map(ThreadItemsListCursor::Opaque),
                    limit: Some(100),
                    sort_direction: Some(SortDirection::Desc),
                    defer_output: (!read_dispatches && turn.is_some()).then_some(
                        ThreadItemOutputDeferral {
                            command_execution: true,
                            mcp_tool_call: true,
                        },
                    ),
                },
            })
            .await
            .map_err(|e| e.to_string())?;
        for entry in page.data {
            if !matches!(
                entry.item.payload,
                ThreadItemPayload::SubAgentActivity { .. }
                    | ThreadItemPayload::AgentMessage { .. }
                    | ThreadItemPayload::Plan { .. }
            ) {
                continue;
            }
            let item_turn_id = entry.turn_id.clone();
            let item = ThreadItem::try_from(entry).map_err(|e| e.to_string())?;
            match &item.payload {
                ThreadItemPayload::SubAgentActivity { .. } => activities.push(item),
                ThreadItemPayload::AgentMessage { .. } | ThreadItemPayload::Plan { .. } => {
                    if let Some(turn) = &mut turn
                        && turn.id == item_turn_id
                        && !turn.items.iter().any(|old| old.id() == item.id())
                    {
                        turn.items.push(item);
                    }
                }
                _ => (),
            }
        }
        if !read_dispatches
            && turn.as_ref().is_some_and(|t| {
                t.items.iter().any(|i| {
                    matches!(
                        i.payload,
                        ThreadItemPayload::AgentMessage { .. } | ThreadItemPayload::Plan { .. }
                    )
                })
            })
        {
            break;
        }
        cursor = page.next_cursor.filter(|next| seen.insert(next.clone()));
        if cursor.is_none() {
            break;
        }
    }
    if let Some(turn) = &mut turn {
        turn.items
            .sort_by_key(|item| item.completed_at_ms.or(item.started_at_ms));
    }
    activities.reverse();
    Ok(TaskSnapshot {
        thread: response.thread,
        turn,
        activities,
    })
}

impl App {
    pub(super) fn refresh_intent_task_panel(&mut self, server: &AppServerSession) {
        let Some(root) = self.primary_thread_id else {
            return;
        };
        if self.intent_task_panel.root != Some(root) {
            if self.intent_task_panel.root.is_some() {
                self.intent_task_panel = IntentTaskPanelState::default();
            }
            self.intent_task_panel.root = Some(root);
            // Restored descendants are not necessarily loaded or subscribed. Reuse the
            // root-scoped persisted discovery used by /subagents before waiting for live events.
            self.refresh_agent_picker_threads(server, root);
        }
        for (id, entry) in self
            .agent_navigation
            .ordered_path_backed_subagent_threads(Some(root))
        {
            if let Some(path) = &entry.agent_path {
                self.intent_task_panel.tasks.discover(&id.to_string(), path);
            }
        }
        if self.intent_task_panel.tasks.tasks.is_empty()
            || self.intent_task_panel.request.is_some()
            || self
                .intent_task_panel
                .last_refresh
                .is_some_and(|last| last.elapsed() < Duration::from_secs(3))
        {
            return;
        }
        let mut ids: Vec<_> = self
            .intent_task_panel
            .tasks
            .tasks
            .keys()
            .filter_map(|id| ThreadId::from_string(id).ok())
            .collect();
        if !self.intent_task_panel.hydrated.contains(&root) {
            ids.push(root);
        }
        let targets: Vec<_> = ids
            .into_iter()
            .map(|id| (id, !self.intent_task_panel.hydrated.contains(&id)))
            .collect();
        let request_id = Uuid::new_v4();
        self.intent_task_panel.request = Some(request_id);
        self.intent_task_panel.last_refresh = Some(Instant::now());
        let handle = server.request_handle();
        let tx = self.app_event_tx.clone();
        tokio::spawn(async move {
            let snapshots = futures::stream::iter(targets)
                .map(|(id, read_dispatches)| {
                    let handle = handle.clone();
                    async move {
                        let result = tokio::time::timeout(
                            Duration::from_secs(15),
                            load_snapshot(handle, id, read_dispatches),
                        )
                        .await
                        .unwrap_or_else(|_| Err("task panel refresh timed out".to_string()));
                        (id, result)
                    }
                })
                .buffer_unordered(8)
                .collect()
                .await;
            tx.send(AppEvent::IntentTasksLoaded {
                root,
                request_id,
                snapshots,
            });
        });
    }

    pub(super) fn apply_intent_task_snapshots(
        &mut self,
        root: ThreadId,
        request_id: Uuid,
        snapshots: Vec<(ThreadId, Result<TaskSnapshot, String>)>,
    ) {
        if self.primary_thread_id != Some(root)
            || self.intent_task_panel.request != Some(request_id)
        {
            return;
        }
        self.intent_task_panel.request = None;
        for (id, snapshot) in snapshots {
            match snapshot {
                Ok(snapshot) => {
                    self.intent_task_panel.hydrated.insert(id);
                    for activity in &snapshot.activities {
                        // Forked histories can retain dispatches from a different root. Only use
                        // history for descendants independently discovered in this root's tree.
                        if let ThreadItemPayload::SubAgentActivity {
                            agent_thread_id, ..
                        } = &activity.payload
                            && self
                                .intent_task_panel
                                .tasks
                                .tasks
                                .contains_key(agent_thread_id)
                        {
                            self.intent_task_panel.tasks.activity(activity);
                        }
                    }
                    self.intent_task_panel
                        .tasks
                        .snapshot(&snapshot.thread, snapshot.turn.as_ref());
                }
                Err(error) => {
                    tracing::warn!(%id, %error, "intent task panel refresh failed");
                    if let Some(task) = self.intent_task_panel.tasks.tasks.get_mut(&id.to_string())
                    {
                        task.refresh_error = true;
                    }
                }
            }
        }
        self.refresh_agent_picker();
        self.chat_widget.request_redraw();
    }

    pub(super) fn paint_intent_task_panel(&mut self, tui: &mut tui::Tui) {
        let rows = self.intent_task_panel.tasks.rows();
        let now = crate::intent_tasks::now_ms();
        let height = tui.terminal.size().map(|size| size.height).unwrap_or(24);
        let limit = usize::from(height.saturating_sub(12) / 5).min(3);
        let mut lines = Vec::new();
        if !rows.is_empty() {
            lines.push(Line::from(format!(
                "子任务 ({}) · /subagents 查看全部及详情",
                rows.len()
            )));
            for task in rows.iter().take(limit) {
                lines.extend(task.lines(now).into_iter().map(Line::from));
            }
            if rows.len() > limit {
                lines.push(Line::from(format!(
                    "另有 {} 个子任务 · /subagents",
                    rows.len() - limit
                )));
            }
            tui.frame_requester()
                .schedule_frame_in(Duration::from_secs(1));
        }
        self.chat_widget.intent_task_panel_lines = lines;
    }
}
