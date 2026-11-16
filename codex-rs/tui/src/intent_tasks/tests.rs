use super::*;
use codex_app_server_protocol::TurnItemsView;

fn turn(id: &str, start: i64, status: TurnStatus) -> Turn {
    Turn {
        id: id.to_string(),
        root_turn_id: None,
        items: Vec::new(),
        items_view: TurnItemsView::Full,
        status,
        error: None,
        started_at: Some(start),
        completed_at: None,
        duration_ms: None,
    }
}

#[test]
fn intent_tasks_completed_run_never_restarts_and_duration_is_frozen() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let running = turn("first", 10, TurnStatus::InProgress);
    task.apply_turn(&running, Some(10_000));
    let mut done = running.clone();
    done.status = TurnStatus::Completed;
    done.duration_ms = Some(2_345);
    done.completed_at = Some(12);
    task.apply_turn(&done, Some(12_500));
    task.apply_turn(&running, Some(20_000));
    task.apply_turn(&done, Some(30_000));
    assert_eq!(task.status, TaskStatus::Completed);
    assert_eq!(task.elapsed(99_000), "0分02秒");
}

#[test]
fn intent_tasks_redispatch_accepts_new_turn_and_rejects_old_failure() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let old = turn("first", 10, TurnStatus::Failed);
    task.apply_turn(&old, None);
    task.apply_turn(&turn("second", 20, TurnStatus::InProgress), None);
    task.apply_turn(&old, None);
    assert_eq!(task.status, TaskStatus::Running);
    assert_eq!(task.turn_id.as_deref(), Some("second"));
    assert!(task.progress.is_none());
    assert_eq!(task.progress_time(30_000), "尚未收到进度");
}

#[test]
fn intent_tasks_untimed_replay_does_not_invent_duration_or_progress() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let mut old = turn("old", 0, TurnStatus::Interrupted);
    old.started_at = None;
    task.apply_turn(&old, None);
    assert_eq!(task.status, TaskStatus::Interrupted);
    assert_eq!(task.elapsed(99_000), "时间未记录");
    assert!(
        task.lines(99_000)
            .iter()
            .any(|line| line.contains("尚未收到进度"))
    );
}

#[test]
fn intent_tasks_titles_hide_internal_paths_and_preserve_nesting() {
    let mut tasks = IntentTasks::default();
    tasks.discover("a", "/root/check_docs");
    tasks.discover("b", "/root/check_docs/review_api");
    tasks.discover("a", "/root/check_docs");
    assert_eq!(tasks.rows().len(), 2);
    assert_eq!(tasks.rows()[1].title, "review api");
    assert!(tasks.rows()[1].lines(0)[0].starts_with("  •"));
    assert!(
        tasks
            .rows()
            .iter()
            .flat_map(|task| task.lines(0))
            .all(|line| !line.contains("/root/"))
    );
}

fn progress(id: &str, text: &str, at: Option<i64>) -> ThreadItem {
    let mut item = ThreadItem::untimed(ThreadItemPayload::Plan {
        id: id.to_string(),
        text: text.to_string(),
    });
    item.completed_at_ms = at;
    item
}

#[test]
fn intent_tasks_interruption_preserves_commentary_as_progress_not_a_result() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    task.apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    task.apply_item("first", &progress("p1", "正在查找源码", Some(12_000)), None);
    task.apply_turn(&turn("first", 10, TurnStatus::Interrupted), None);
    task.apply_item("first", &progress("p2", "正在核对事件", Some(13_000)), None);
    assert!(task.result.is_none());
    assert!(
        task.lines(20_000)
            .iter()
            .any(|line| line.contains("最近进度：正在核对事件"))
    );
}

#[test]
fn intent_tasks_duplicate_and_replayed_progress_do_not_change_receipt_time() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    task.apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    let item = progress("p1", "已完成源码定位", Some(12_000));
    task.apply_item("first", &item, Some(13_000));
    task.apply_item("first", &item, Some(20_000));
    task.apply_item(
        "first",
        &progress("old", "正在查找源码", Some(11_000)),
        None,
    );
    assert_eq!(task.progress.as_deref(), Some("已完成源码定位"));
    assert_eq!(task.progress_time(22_000), "最近收到 9秒前");
    let mut restored = IntentTask::new("child", "/root/check_docs");
    restored.apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    restored.apply_item("first", &item, None);
    assert_eq!(restored.progress_time(22_000), "历史进度记录于 10秒前");
}

#[test]
fn intent_tasks_unknown_historical_order_does_not_replace_a_newer_run() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let mut old = turn("first", 0, TurnStatus::Completed);
    old.started_at = None;
    let mut new = old.clone();
    new.id = "another-opaque-id".into();
    new.status = TurnStatus::InProgress;
    task.apply_turn(&old, None);
    task.apply_turn(&new, None);
    assert_eq!(task.status, TaskStatus::Completed);
}

#[test]
fn intent_tasks_uuid7_order_handles_redispatch_in_the_same_second() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let old = turn(
        "019cff70-2599-75e2-af72-b90000000001",
        10,
        TurnStatus::Completed,
    );
    let new = turn(
        "019cff70-259a-75e2-af72-b90000000001",
        10,
        TurnStatus::InProgress,
    );
    task.apply_turn(&old, None);
    task.apply_turn(&new, None);
    task.apply_turn(&old, None);
    assert_eq!(task.status, TaskStatus::Running);
    assert_eq!(task.turn_id.as_deref(), Some(new.id.as_str()));
}

#[test]
fn intent_tasks_forked_parent_turn_is_not_the_childs_completed_task() {
    let mut task = IntentTask::new("019cff70-259a-75e2-af72-b90000000001", "/root/check_docs");
    let inherited = turn(
        "019cff70-2599-75e2-af72-b90000000001",
        10,
        TurnStatus::Completed,
    );
    task.apply_turn(&inherited, None);
    assert_eq!(task.status, TaskStatus::Starting);
    assert_eq!(task.turn_id, None);
    let child = turn(
        "019cff70-259b-75e2-af72-b90000000001",
        10,
        TurnStatus::InProgress,
    );
    task.apply_turn(&child, None);
    assert_eq!(task.status, TaskStatus::Running);
}

#[test]
fn intent_tasks_dispatch_metadata_never_revives_or_invents_progress() {
    let mut tasks = IntentTasks::default();
    let activity = |id: &str, purpose: Option<&str>, at: i64| ThreadItem {
        payload: ThreadItemPayload::SubAgentActivity {
            task_description: purpose.map(str::to_string),
            model: None,
            reasoning_effort: None,
            id: id.into(),
            kind: codex_app_server_protocol::SubAgentActivityKind::Interacted,
            agent_thread_id: "child".into(),
            agent_path: "/root/check_docs".into(),
        },
        started_at_ms: Some(at),
        completed_at_ms: Some(at),
    };
    tasks.activity(&activity("dispatch", Some("核实配置字段"), 10_000));
    tasks
        .tasks
        .get_mut("child")
        .unwrap()
        .apply_turn(&turn("first", 10, TurnStatus::Completed), None);
    tasks.activity(&activity("followup", Some("复查升级入口"), 30_000));
    tasks.activity(&activity("dispatch", Some("核实配置字段"), 10_000));
    tasks.activity(&activity("message", None, 40_000));
    let task = tasks.tasks.get("child").unwrap();
    assert_eq!(task.status, TaskStatus::Completed);
    assert_eq!(task.purpose.as_deref(), Some("核实配置字段"));
    assert_eq!(task.pending_purpose.as_deref(), Some("复查升级入口"));
    assert!(task.progress.is_none());
    let task = tasks.tasks.get_mut("child").unwrap();
    task.apply_turn(&turn("next", 31, TurnStatus::InProgress), None);
    assert_eq!(task.purpose.as_deref(), Some("复查升级入口"));
    assert_eq!(task.pending_purpose, None);
}

#[test]
fn intent_tasks_lifecycle_records_only_appear_in_explicit_details() {
    use crate::history_cell::HistoryCell;
    let item = ThreadItemPayload::SubAgentActivity {
        task_description: None,
        model: None,
        reasoning_effort: None,
        id: "dispatch".into(),
        kind: codex_app_server_protocol::SubAgentActivityKind::Started,
        agent_thread_id: "child".into(),
        agent_path: "/root/check_docs".into(),
    };
    let cell = crate::multi_agents::sub_agent_activity_history_cell(&item).unwrap();
    for width in [20, 60, 120] {
        assert!(cell.display_lines(width).is_empty());
        assert!(cell.intent_hyperlink_lines(width).is_empty());
        assert!(
            cell.transcript_lines(width)
                .iter()
                .any(|line| line.to_string().contains("/root/check_docs"))
        );
    }
}

#[test]
fn intent_tasks_waiting_uses_actual_flags_and_cannot_revive_a_failed_task() {
    let mut tasks = IntentTasks::default();
    tasks.discover("child", "/root/check_docs");
    tasks
        .tasks
        .get_mut("child")
        .unwrap()
        .apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    let waiting = ServerNotification::ThreadStatusChanged(
        codex_app_server_protocol::ThreadStatusChangedNotification {
            thread_id: "child".into(),
            status: ThreadStatus::Active {
                active_flags: vec![codex_app_server_protocol::ThreadActiveFlag::WaitingOnApproval],
            },
        },
    );
    tasks.notification("child", &waiting, 11_000);
    assert_eq!(tasks.tasks["child"].status, TaskStatus::Waiting);
    tasks
        .tasks
        .get_mut("child")
        .unwrap()
        .apply_turn(&turn("first", 10, TurnStatus::Failed), None);
    tasks.notification("child", &waiting, 12_000);
    assert_eq!(tasks.tasks["child"].status, TaskStatus::Failed);
}
