//! Display-only V2 task aggregation. No tool arguments or outputs enter this model.
//!
//! A thread can run several tasks. Only an authoritative newer turn can replace its current
//! run; lifecycle hints and stale snapshots cannot revive a terminal run.

use codex_app_server_protocol::ServerNotification;
use codex_app_server_protocol::Thread;
use codex_app_server_protocol::ThreadItem;
use codex_app_server_protocol::ThreadItemPayload;
use codex_app_server_protocol::ThreadStatus;
use codex_app_server_protocol::Turn;
use codex_app_server_protocol::TurnStatus;
use std::collections::BTreeMap;
use std::collections::HashSet;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TaskStatus {
    Starting,
    Running,
    Waiting,
    Completed,
    Failed,
    Interrupted,
}

impl TaskStatus {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Starting => "启动中",
            Self::Running => "运行中",
            Self::Waiting => "等待",
            Self::Completed => "完成",
            Self::Failed => "失败",
            Self::Interrupted => "中断",
        }
    }

    fn terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Interrupted)
    }
}

#[derive(Clone, Debug)]
pub(crate) struct IntentTask {
    pub(crate) thread_id: String,
    pub(crate) path: String,
    pub(crate) title: String,
    pub(crate) purpose: Option<String>,
    purpose_at: Option<i64>,
    purpose_id: Option<String>,
    pending_purpose: Option<String>,
    pub(crate) turn_id: Option<String>,
    prior_turns: HashSet<String>,
    pub(crate) status: TaskStatus,
    started_ms: Option<i64>,
    completed_ms: Option<i64>,
    duration_ms: Option<i64>,
    finished_received_ms: Option<i64>,
    uncertain_end: bool,
    pub(crate) current: String,
    pub(crate) progress: Option<String>,
    progress_id: Option<String>,
    progress_at: Option<i64>,
    received_at: Option<i64>,
    pub(crate) result: Option<String>,
    pub(crate) refresh_error: bool,
}

/// A short excerpt of actual authored text, never an inferred description of tool activity.
pub(crate) fn excerpt(text: &str) -> Option<String> {
    let line = text.lines().map(str::trim).find(|line| !line.is_empty())?;
    let clean: String = line
        .chars()
        .filter(|ch| !ch.is_control())
        .take(180)
        .collect();
    (!clean.is_empty()).then_some(clean)
}

impl IntentTask {
    fn new(thread_id: &str, path: &str) -> Self {
        Self {
            thread_id: thread_id.to_owned(),
            path: path.to_owned(),
            title: path
                .rsplit('/')
                .next()
                .unwrap_or("子任务")
                .replace('_', " "),
            purpose: None,
            purpose_at: None,
            purpose_id: None,
            pending_purpose: None,
            turn_id: None,
            prior_turns: HashSet::new(),
            status: TaskStatus::Starting,
            started_ms: None,
            completed_ms: None,
            duration_ms: None,
            finished_received_ms: None,
            uncertain_end: false,
            current: "等待任务开始记录".to_string(),
            progress: None,
            progress_id: None,
            progress_at: None,
            received_at: None,
            result: None,
            refresh_error: false,
        }
    }

    fn apply_turn(&mut self, turn: &Turn, live_at: Option<i64>) {
        // Full-history forks retain ancestor turns. UUIDv7 thread/turn identities are generated
        // in creation order; an ancestor turn predating this thread is not this child's task.
        if uuid::Uuid::parse_str(&self.thread_id)
            .ok()
            .zip(uuid::Uuid::parse_str(&turn.id).ok())
            .is_some_and(|(thread, turn)| {
                thread.get_version_num() == 7 && turn.get_version_num() == 7 && turn < thread
            })
        {
            return;
        }
        if self.turn_id.as_deref() != Some(&turn.id) {
            if self.prior_turns.contains(&turn.id) {
                return;
            }
            let started_ms = turn.started_at.map(|time| time.saturating_mul(1_000));
            if self.turn_id.is_some() {
                // Recorded times take precedence. UUIDv7 provides order when timestamps are absent
                // or rounded to the same second. Opaque old IDs cannot safely reorder history.
                let newer = match (started_ms, self.started_ms) {
                    (Some(new), Some(old)) if new != old => new > old,
                    _ => self.turn_id.as_ref().is_some_and(|old| {
                        uuid::Uuid::parse_str(old)
                            .ok()
                            .zip(uuid::Uuid::parse_str(&turn.id).ok())
                            .is_some_and(|(old, new)| {
                                old.get_version_num() == 7
                                    && new.get_version_num() == 7
                                    && new > old
                            })
                    }),
                };
                if !newer {
                    return;
                }
            }
            if let Some(old) = self.turn_id.replace(turn.id.clone()) {
                self.prior_turns.insert(old);
                if !self
                    .purpose_at
                    .zip(started_ms)
                    .is_some_and(|(dispatch, start)| dispatch >= start - 1_000)
                {
                    self.purpose = None;
                }
            }
            self.started_ms = started_ms;
            self.completed_ms = None;
            self.duration_ms = None;
            self.finished_received_ms = None;
            self.uncertain_end = false;
            self.status = TaskStatus::Running;
            if let Some(purpose) = self.pending_purpose.take() {
                self.purpose = Some(purpose);
            }
            self.progress = None;
            self.progress_id = None;
            self.progress_at = None;
            self.received_at = None;
            self.result = None;
            self.current = "尚未收到进度".to_string();
        }
        // Old TurnStarted and polling snapshots may arrive after TurnCompleted.
        if self.status.terminal() && turn.status == TurnStatus::InProgress {
            return;
        }
        self.started_ms = turn
            .started_at
            .map(|t| t.saturating_mul(1_000))
            .or(self.started_ms);
        for item in &turn.items {
            self.apply_item(&turn.id, item, live_at);
        }
        if !self.status.terminal() {
            // Upstream normalizes unfinished persisted turns to Interrupted when the live
            // thread becomes idle. Persistence can lag completion, so this is not an actual
            // interruption event. Wait for terminal metadata or a live completion instead of
            // permanently latching the inferred status. Old incomplete history is explicit too.
            self.uncertain_end = live_at.is_none()
                && turn.status == TurnStatus::Interrupted
                && turn.completed_at.is_none()
                && turn.duration_ms.is_none()
                && turn.error.is_none();
            if self.uncertain_end {
                self.status = TaskStatus::Waiting;
                self.current = "结束状态尚未确认；等待完整记录".to_string();
                return;
            }
            self.status = match turn.status {
                TurnStatus::InProgress => TaskStatus::Running,
                TurnStatus::Completed => TaskStatus::Completed,
                TurnStatus::Failed => TaskStatus::Failed,
                TurnStatus::Interrupted => TaskStatus::Interrupted,
            };
            if self.status == TaskStatus::Running {
                self.current = self
                    .progress
                    .as_deref()
                    .and_then(excerpt)
                    .unwrap_or_else(|| "尚未收到进度".to_string());
            }
        }
        if self.status.terminal() {
            self.finished_received_ms = self.finished_received_ms.or(live_at);
            // Set the end once. Duplicate completions never restart the clock.
            self.completed_ms = self
                .completed_ms
                .or_else(|| turn.completed_at.map(|t| t.saturating_mul(1_000)));
            self.duration_ms = self
                .duration_ms
                .or(turn.duration_ms.filter(|t| *t >= 0))
                .or_else(|| {
                    self.started_ms
                        .zip(self.completed_ms)
                        .map(|(start, end)| (end - start).max(0))
                });
            if turn.error.is_some() {
                self.result = Some(format!(
                    "任务{}；打开子任务详情查看原因",
                    self.status.label()
                ));
            }
            self.current = format!("本次任务已{}", self.status.label());
        }
    }

    fn apply_item(&mut self, turn_id: &str, item: &ThreadItem, live_at: Option<i64>) {
        if self.turn_id.as_deref() != Some(turn_id) {
            return;
        }
        let text = match &item.payload {
            ThreadItemPayload::AgentMessage { text, .. } | ThreadItemPayload::Plan { text, .. } => {
                text
            }
            _ => return,
        };
        let Some(summary) = excerpt(text) else {
            return;
        };
        let recorded_at = item.completed_at_ms.or(item.started_at_ms);
        let same_item = self.progress_id.as_deref() == Some(item.id());
        if self
            .progress_at
            .zip(recorded_at)
            .is_some_and(|(old, new)| new < old)
        {
            return;
        }
        if same_item && self.progress.as_ref() == Some(text) {
            return;
        }
        // Untimed historical items cannot displace timed or live progress from a newer source.
        if !same_item
            && recorded_at.is_none()
            && live_at.is_none()
            && (self.progress_at.is_some() || self.received_at.is_some())
        {
            return;
        }
        // Keep authored content for the details popup; compact surfaces use excerpts.
        self.progress = Some(text.clone());
        if matches!(
            &item.payload,
            ThreadItemPayload::AgentMessage {
                phase: Some(codex_protocol::models::MessagePhase::FinalAnswer),
                ..
            }
        ) {
            self.result = Some(text.clone());
        }
        self.progress_id = Some(item.id().to_string());
        self.progress_at = recorded_at;
        self.received_at = live_at;
        if matches!(self.status, TaskStatus::Starting | TaskStatus::Running) {
            self.current = summary.clone();
        }
    }

    pub(crate) fn elapsed(&self, now_ms: i64) -> String {
        if self.uncertain_end {
            return "结束时间未确认".to_string();
        }
        let duration = if self.status.terminal() {
            self.duration_ms
        } else {
            self.started_ms
                .map(|start| now_ms.saturating_sub(start).max(0))
        };
        duration
            .map(|ms| format!("{}分{:02}秒", ms / 60_000, ms / 1_000 % 60))
            .unwrap_or_else(|| "时间未记录".to_string())
    }

    pub(crate) fn progress_time(&self, now_ms: i64) -> String {
        if self.progress.is_none() {
            return "尚未收到进度".to_string();
        }
        let (label, at) = if let Some(at) = self.received_at {
            ("最近收到", Some(at))
        } else {
            ("历史进度记录于", self.progress_at)
        };
        at.map(|at| format!("{label} {}秒前", now_ms.saturating_sub(at).max(0) / 1_000))
            .unwrap_or_else(|| "历史进度时间未记录".to_string())
    }

    pub(crate) fn lines(&self, now_ms: i64) -> Vec<String> {
        let depth = self.path.matches('/').count().saturating_sub(2).min(5);
        let indent = "  ".repeat(depth);
        let mut lines = vec![
            format!(
                "{indent}• {} · {} · {}{}",
                self.title,
                self.status.label(),
                self.elapsed(now_ms),
                if self.refresh_error {
                    " · 状态刷新失败"
                } else {
                    ""
                }
            ),
            format!(
                "{indent}  任务：{}",
                self.purpose.as_deref().unwrap_or("任务说明未记录或不可读")
            ),
            format!("{indent}  当前：{}", self.current),
            format!(
                "{indent}  {}：{}",
                if self.status.terminal() && self.result.is_some() {
                    "结果"
                } else {
                    "最近进度"
                },
                if self.status.terminal() && self.result.is_some() {
                    self.result.as_deref().unwrap_or("未记录结果摘要")
                } else {
                    self.progress.as_deref().unwrap_or("尚未收到进度")
                }
            ),
            format!("{indent}  {}", self.progress_time(now_ms)),
        ];
        if let Some(pending) = &self.pending_purpose {
            lines[2] = format!("{indent}  后续任务待开始：{pending}");
        }
        lines
    }

    pub(crate) fn compact_elapsed(&self, now_ms: i64) -> String {
        if self.uncertain_end {
            return "--:--".to_string();
        }
        let duration = if self.status.terminal() {
            self.duration_ms
        } else {
            self.started_ms
                .map(|start| now_ms.saturating_sub(start).max(0))
        };
        duration.map_or_else(
            || "--:--".to_string(),
            |duration| {
                let seconds = duration / 1_000;
                format!("{:02}:{:02}", seconds / 60, seconds % 60)
            },
        )
    }

    fn finished_at(&self) -> Option<i64> {
        self.completed_ms
            .or_else(|| {
                self.started_ms
                    .zip(self.duration_ms)
                    .map(|(a, b)| a.saturating_add(b))
            })
            .or(self.finished_received_ms)
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct IntentTasks {
    pub(crate) tasks: BTreeMap<String, IntentTask>,
    parent_finished_ms: Option<i64>,
    retired: HashSet<(String, String)>,
}

impl IntentTasks {
    pub(crate) fn discover(&mut self, thread_id: &str, path: &str) {
        if path.starts_with("/root/") {
            self.tasks
                .entry(thread_id.to_string())
                .or_insert_with(|| IntentTask::new(thread_id, path));
        }
    }

    pub(crate) fn activity(&mut self, item: &ThreadItem) {
        if let ThreadItemPayload::SubAgentActivity {
            id,
            agent_thread_id,
            agent_path,
            task_description,
            ..
        } = &item.payload
        {
            self.discover(agent_thread_id, agent_path);
            if let Some(task) = self.tasks.get_mut(agent_thread_id)
                && let Some(description) = task_description
                    .as_ref()
                    .filter(|text| !text.trim().is_empty())
            {
                let at = item.completed_at_ms.or(item.started_at_ms);
                if task.purpose_id.as_ref() != Some(id)
                    && (task.purpose.is_none()
                        || at.zip(task.purpose_at).is_some_and(|(new, old)| new >= old))
                {
                    if task.status.terminal()
                        && task.purpose.is_some()
                        && task
                            .completed_ms
                            .zip(at)
                            .is_none_or(|(end, dispatch)| dispatch >= end + 1_000)
                    {
                        task.pending_purpose = Some(description.clone());
                    } else {
                        task.purpose = Some(description.clone());
                    }
                    task.purpose_at = at;
                    task.purpose_id = Some(id.clone());
                }
            }
        }
    }

    pub(crate) fn snapshot(&mut self, thread: &Thread, turn: Option<&Turn>) {
        if let Some(task) = self.tasks.get_mut(&thread.id) {
            task.refresh_error = false;
            if let Some(turn) = turn {
                task.apply_turn(turn, None);
            }
            if !task.status.terminal()
                && let ThreadStatus::Active { active_flags } = &thread.status
                && !active_flags.is_empty()
            {
                task.status = TaskStatus::Waiting;
                task.current = "等待审批或用户输入；打开子任务详情处理".to_string();
            }
        }
    }

    pub(crate) fn notification(
        &mut self,
        thread_id: &str,
        notification: &ServerNotification,
        now_ms: i64,
    ) {
        match notification {
            ServerNotification::ItemStarted(n) => self.activity(&n.item),
            ServerNotification::ItemCompleted(n) => self.activity(&n.item),
            _ => (),
        }
        let Some(task) = self.tasks.get_mut(thread_id) else {
            return;
        };
        match notification {
            ServerNotification::TurnStarted(n) => {
                task.apply_turn(&n.turn, Some(now_ms));
            }
            ServerNotification::TurnCompleted(n) => {
                task.apply_turn(&n.turn, Some(now_ms));
            }
            ServerNotification::ItemCompleted(n) => {
                task.apply_item(&n.turn_id, &n.item, Some(now_ms))
            }
            ServerNotification::ThreadStatusChanged(n) if !task.status.terminal() => {
                if let ThreadStatus::Active { active_flags } = &n.status {
                    task.status = if active_flags.is_empty() {
                        TaskStatus::Running
                    } else {
                        TaskStatus::Waiting
                    };
                    if !active_flags.is_empty() {
                        task.current = "等待审批或用户输入；打开子任务详情处理".to_string();
                    }
                }
            }
            _ => (),
        }
    }

    pub(crate) fn rows(&self) -> Vec<IntentTask> {
        let mut rows: Vec<_> = self.tasks.values().cloned().collect();
        rows.sort_by(|a, b| a.path.cmp(&b.path));
        rows
    }

    /// Finishing the parent's reply acknowledges already finished runs. Retain the records for
    /// details and replay; a new turn on the same child is a different visible run.
    pub(crate) fn parent_turn(&mut self, turn: &Turn, live_at: Option<i64>) {
        if turn.status != TurnStatus::Completed {
            return;
        }
        let end = turn
            .completed_at
            .map(|at| at.saturating_mul(1_000))
            .or_else(|| {
                turn.started_at
                    .zip(turn.duration_ms)
                    .map(|(at, duration)| at.saturating_mul(1_000).saturating_add(duration))
            })
            .or(live_at);
        if let Some(end) = end {
            self.parent_finished_ms = Some(self.parent_finished_ms.map_or(end, |old| old.max(end)));
        }
        for task in self.tasks.values().filter(|task| task.status.terminal()) {
            if task
                .finished_at()
                .zip(end)
                .is_some_and(|(finished, parent)| finished <= parent)
                && let Some(turn_id) = &task.turn_id
            {
                self.retired
                    .insert((task.thread_id.clone(), turn_id.clone()));
            }
        }
    }

    pub(crate) fn visible_rows(&self) -> Vec<IntentTask> {
        self.rows()
            .into_iter()
            .filter(|task| {
                let retired = task.turn_id.as_ref().is_some_and(|turn| {
                    self.retired
                        .contains(&(task.thread_id.clone(), turn.clone()))
                });
                let acknowledged_history = task.status.terminal()
                    && task
                        .finished_at()
                        .zip(self.parent_finished_ms)
                        .is_some_and(|(finished, parent)| finished <= parent);
                !retired && !acknowledged_history
            })
            .collect()
    }
}

pub(crate) fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

#[cfg(test)]
mod tests;
