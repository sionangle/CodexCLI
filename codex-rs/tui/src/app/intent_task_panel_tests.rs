use super::intent_task_panel::TaskSnapshot;
use super::*;
use crate::intent_tasks::TaskStatus;
use codex_app_server_protocol::ThreadStatus;
use codex_app_server_protocol::TurnItemsView;

fn mouse(kind: crossterm::event::MouseEventKind, x: u16, y: u16) -> TuiEvent {
    TuiEvent::Mouse(crossterm::event::MouseEvent {
        kind,
        column: x,
        row: y,
        modifiers: crossterm::event::KeyModifiers::NONE,
    })
}

#[tokio::test]
async fn intent_task_click_opens_details_esc_restores_draft_and_drag_does_not_open() {
    use crate::render::renderable::Renderable;
    use crossterm::event::MouseButton::Left;
    use crossterm::event::MouseEventKind::{Down, Drag, Up};
    let mut app = super::test_support::make_test_app().await;
    let mut tui = crate::tui::test_support::make_test_tui().unwrap();
    app.chat_widget.insert_str("keep this draft");
    app.intent_task_panel
        .tasks
        .discover("child", "/root/check_docs");
    app.intent_task_panel
        .tasks
        .tasks
        .get_mut("child")
        .unwrap()
        .purpose = Some("核对真实任务说明\n保留第二行".into());
    app.paint_intent_task_panel(&mut tui);
    let area = Rect::new(0, 8, 60, 1);
    app.chat_widget
        .intent_task_rows
        .render(area, &mut ratatui::buffer::Buffer::empty(area));
    assert!(
        !app.handle_intent_detail_event(&mut tui, &mouse(Down(Left), 3, 8))
            .unwrap()
    );
    app.handle_intent_detail_event(&mut tui, &mouse(Drag(Left), 4, 8))
        .unwrap();
    assert!(
        !app.handle_intent_detail_event(&mut tui, &mouse(Up(Left), 3, 8))
            .unwrap()
    );
    assert!(app.overlay.is_none());
    app.handle_intent_detail_event(&mut tui, &mouse(Down(Left), 3, 8))
        .unwrap();
    assert!(
        app.handle_intent_detail_event(&mut tui, &mouse(Up(Left), 3, 8))
            .unwrap()
    );
    let Some(Overlay::Static(overlay)) = &mut app.overlay else {
        panic!("missing task popup");
    };
    let area = Rect::new(0, 0, 80, 20);
    let mut buffer = ratatui::buffer::Buffer::empty(area);
    overlay.render(area, &mut buffer);
    let text = buffer
        .content
        .iter()
        .map(|c| c.symbol())
        .collect::<String>()
        .replace(' ', "");
    assert!(text.contains("核对真实任务说明"));
    assert!(text.contains("保留第二行"));
    assert!(text.contains("/root/check_docs"));
    app.overlay
        .as_mut()
        .unwrap()
        .handle_event(
            &mut tui,
            TuiEvent::Key(crossterm::event::KeyEvent::new(
                crossterm::event::KeyCode::Esc,
                crossterm::event::KeyModifiers::NONE,
            )),
        )
        .unwrap();
    assert!(app.overlay.as_ref().unwrap().is_done());
    app.close_transcript_overlay(&mut tui);
    assert!(app.overlay.is_none());
    assert_eq!(
        app.chat_widget.composer_text_with_pending(),
        "keep this draft"
    );
}

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
    let rows = &app.chat_widget.intent_task_rows.rows;
    assert_eq!(rows.len(), 2);
    let text = rows
        .iter()
        .map(|row| format!("{} {}", row.title, row.elapsed))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(text.contains("check source 00:02"));
    assert!(!text.contains("任务说明"));
    assert!(!text.contains("/root/"));
    assert!(!text.contains("parent inherited text"));
}
