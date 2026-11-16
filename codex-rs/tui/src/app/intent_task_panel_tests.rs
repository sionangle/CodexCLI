use super::intent_task_panel::TaskSnapshot;
use super::*;
use crate::intent_tasks::TaskStatus;
use codex_app_server_protocol::ThreadStatus;
use codex_app_server_protocol::TurnItemsView;

#[tokio::test]
async fn intent_task_panel_rejects_stale_root_refresh_and_renders_parallel_rows() {
    let mut app = super::test_support::make_test_app().await;
    let root = ThreadId::new();
    let first = ThreadId::new();
    let second = ThreadId::new();
    app.primary_thread_id = Some(root);
    app.intent_task_panel.root = Some(root);
    app.intent_task_panel
        .tasks
        .discover(&first.to_string(), "/root/check_source");
    app.intent_task_panel
        .tasks
        .discover(&second.to_string(), "/root/check_docs");
    let request_id = Uuid::new_v4();
    app.intent_task_panel.request = Some(request_id);
    let thread = super::agents_overview::tests::overview_thread(
        first,
        Some(root),
        "parent inherited text",
        ThreadStatus::Idle,
    );
    let turn = Turn {
        id: ThreadId::new().to_string(),
        root_turn_id: None,
        items: Vec::new(),
        items_view: TurnItemsView::Full,
        status: TurnStatus::Completed,
        error: None,
        started_at: Some(10),
        completed_at: Some(12),
        duration_ms: Some(2_000),
    };
    app.apply_intent_task_snapshots(
        ThreadId::new(),
        request_id,
        vec![(
            first,
            Ok(TaskSnapshot {
                thread: thread.clone(),
                turn: Some(turn.clone()),
                activities: Vec::new(),
            }),
        )],
    );
    assert_eq!(
        app.intent_task_panel.tasks.tasks[&first.to_string()].status,
        TaskStatus::Starting
    );
    app.apply_intent_task_snapshots(
        root,
        request_id,
        vec![(
            first,
            Ok(TaskSnapshot {
                thread,
                turn: Some(turn),
                activities: Vec::new(),
            }),
        )],
    );
    assert_eq!(
        app.intent_task_panel.tasks.tasks[&first.to_string()].status,
        TaskStatus::Completed
    );
    assert!(
        app.intent_task_panel.tasks.tasks[&first.to_string()]
            .purpose
            .is_none()
    );
    let mut tui = crate::tui::test_support::make_test_tui().unwrap();
    app.paint_intent_task_panel(&mut tui);
    let text = app
        .chat_widget
        .intent_task_panel_lines
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(text.contains("子任务 (2)"));
    assert!(!text.contains("/root/"));
    assert!(!text.contains("parent inherited text"));
}
