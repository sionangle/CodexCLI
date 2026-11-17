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
    assert_eq!(task.status, TaskStatus::Waiting);
    assert_eq!(task.elapsed(99_000), "结束时间未确认");
    assert!(
        task.lines(99_000)
            .iter()
            .any(|line| line.contains("尚未收到进度"))
    );
}

#[test]
fn intent_tasks_inferred_interruption_cannot_latch_before_persisted_completion() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    let running = turn("first", 10, TurnStatus::InProgress);
    task.apply_turn(&running, Some(10_000));
    let inferred = turn("first", 10, TurnStatus::Interrupted);
    task.apply_turn(&inferred, None);
    assert_eq!(task.status, TaskStatus::Waiting);
    assert!(!task.status.terminal());
    assert_eq!(task.elapsed(30_000), "结束时间未确认");
    let mut completed = turn("first", 10, TurnStatus::Completed);
    completed.completed_at = Some(12);
    completed.duration_ms = Some(2_345);
    task.apply_turn(&completed, None);
    task.apply_turn(&inferred, None);
    task.apply_turn(&running, Some(40_000));
    assert_eq!(task.status, TaskStatus::Completed);
    assert_eq!(task.elapsed(99_000), "0分02秒");
}

#[test]
fn intent_tasks_real_interruption_stays_terminal_without_optional_timestamps() {
    let mut task = IntentTask::new("child", "/root/check_docs");
    task.apply_turn(&turn("first", 10, TurnStatus::Interrupted), Some(12_000));
    task.apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    assert_eq!(task.status, TaskStatus::Interrupted);
    assert_eq!(task.elapsed(99_000), "时间未记录");
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
fn intent_tasks_parent_reply_retires_finished_runs_but_keeps_archive_and_new_dispatch() {
    let mut tasks = IntentTasks::default();
    for id in ["done", "failed", "interrupted", "active"] {
        tasks.discover(id, &format!("/root/{id}"));
    }
    for (id, status) in [
        ("done", TurnStatus::Completed),
        ("failed", TurnStatus::Failed),
        ("interrupted", TurnStatus::Interrupted),
        ("active", TurnStatus::InProgress),
    ] {
        let mut child = turn("first", 10, status.clone());
        if status != TurnStatus::InProgress {
            child.completed_at = Some(12);
            child.duration_ms = Some(2000);
        }
        tasks
            .tasks
            .get_mut(id)
            .unwrap()
            .apply_turn(&child, Some(12000));
    }
    assert_eq!(tasks.visible_rows().len(), 4);
    let mut parent = turn("reply", 9, TurnStatus::Completed);
    parent.completed_at = Some(15);
    tasks.parent_turn(&parent, Some(15000));
    assert_eq!(
        tasks
            .visible_rows()
            .iter()
            .map(|t| t.thread_id.as_str())
            .collect::<Vec<_>>(),
        vec!["active"]
    );
    assert_eq!(tasks.rows().len(), 4);
    tasks
        .tasks
        .get_mut("done")
        .unwrap()
        .apply_turn(&turn("second", 20, TurnStatus::InProgress), Some(20000));
    tasks.parent_turn(&parent, Some(25000));
    assert_eq!(tasks.visible_rows().len(), 2);
    let mut new_done = turn("second", 20, TurnStatus::Completed);
    new_done.completed_at = Some(24);
    tasks
        .tasks
        .get_mut("done")
        .unwrap()
        .apply_turn(&new_done, Some(24000));
    tasks.parent_turn(&parent, Some(30000));
    assert_eq!(
        tasks.visible_rows().len(),
        2,
        "stale parent completion must not retire a newer run"
    );
}

#[test]
fn intent_tasks_replay_order_and_late_child_completion_respect_parent_reply_time() {
    for parent_first in [false, true] {
        let mut tasks = IntentTasks::default();
        tasks.discover("child", "/root/review");
        let mut parent = turn("parent", 9, TurnStatus::Completed);
        parent.completed_at = Some(20);
        let mut child = turn("child", 10, TurnStatus::Completed);
        child.completed_at = Some(15);
        if parent_first {
            tasks.parent_turn(&parent, None);
        }
        tasks
            .tasks
            .get_mut("child")
            .unwrap()
            .apply_turn(&child, None);
        if !parent_first {
            tasks.parent_turn(&parent, None);
        }
        assert!(tasks.visible_rows().is_empty());
        let mut late = turn("late", 21, TurnStatus::Completed);
        late.completed_at = Some(25);
        tasks
            .tasks
            .get_mut("child")
            .unwrap()
            .apply_turn(&late, None);
        assert_eq!(tasks.visible_rows().len(), 1);
    }
}

#[test]
fn intent_tasks_details_retain_full_authored_text_and_duplicate_receipt_time() {
    let mut task = IntentTask::new("child", "/root/review");
    task.apply_turn(&turn("first", 10, TurnStatus::InProgress), None);
    let full = format!("第一阶段\n{}\n最终细节", "完整内容".repeat(100));
    let item = progress("plan", &full, Some(12000));
    task.apply_item("first", &item, Some(13000));
    task.apply_item("first", &item, Some(20000));
    assert_eq!(task.progress.as_deref(), Some(full.as_str()));
    assert_eq!(task.current, "第一阶段");
    assert_eq!(task.progress_time(22000), "最近收到 9秒前");
    assert_eq!(task.compact_elapsed(22000), "00:12");
}

#[test]
fn intent_tasks_failed_details_show_the_recorded_error() {
    let mut task = IntentTask::new("child", "/root/review");
    let mut failed = turn("first", 10, TurnStatus::Failed);
    failed.error = Some(codex_app_server_protocol::TurnError {
        message: "真实失败原因".into(),
        additional_details: Some("具体诊断信息".into()),
        codex_error_info: None,
        misalignment: None,
    });
    task.apply_turn(&failed, Some(12000));
    assert_eq!(task.result.as_deref(), Some("真实失败原因\n具体诊断信息"));
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
