use super::*;
use crate::exec_cell::CommandOutput;
use crate::exec_cell::new_active_exec_command;
use codex_app_server_protocol::CommandExecutionSource;

fn source() -> crate::exec_cell::ExecCell {
    let mut cell = new_active_exec_command(
        "retained-intent".into(),
        vec!["echo".into(), "hidden_command".into()],
        Vec::new(),
        CommandExecutionSource::Agent,
        None,
        false,
    );
    cell.complete_call(
        "retained-intent",
        CommandOutput::new(0, "hidden_output\n".repeat(200)),
        std::time::Duration::from_secs(1),
    );
    cell
}

#[test]
fn intent_retained_layout_survives_resize_disclosure_and_history_reconstruction() {
    let cells: Vec<Arc<dyn HistoryCell>> = vec![Arc::new(source())];
    let mut view = TranscriptView::default();
    view.set_presentation(false, HistoryRenderMode::Intent);
    for width in [20, 100, 35] {
        tests::render(&mut view, &cells, width, 12);
        let text = view.base_layout(&cells, 0).unwrap();
        assert!(!text.text().contains("hidden_command"));
        assert!(!text.text().contains("hidden_output"));
    }
    view.disclosure.expanded.extend(cells[0].activity_ids());
    tests::render(&mut view, &cells, 100, 12);
    assert!(
        view.base_layout(&cells, 0)
            .unwrap()
            .text()
            .contains("hidden_output")
    );
    view.set_presentation(true, HistoryRenderMode::Rich);
    tests::render(&mut view, &cells, 100, 12);
    assert!(
        view.base_layout(&cells, 0)
            .unwrap()
            .text()
            .contains("hidden_command")
    );
    // A passive pane can carry its previous detailed flag when the global mode changes.
    view.sync_render_mode(HistoryRenderMode::Intent);
    assert!(!view.is_detailed());
    tests::render(&mut view, &cells, 100, 12);
    assert!(
        !view
            .base_layout(&cells, 0)
            .unwrap()
            .text()
            .contains("hidden_command")
    );
    // Reconstructed cells use the same presentation contract as live completion.
    let replayed: Vec<Arc<dyn HistoryCell>> = vec![Arc::new(source())];
    tests::render(&mut view, &replayed, 40, 12);
    assert!(
        !view
            .base_layout(&replayed, 0)
            .unwrap()
            .text()
            .contains("hidden_output")
    );
}

#[test]
fn intent_passive_activity_preview_does_not_show_raw_live_output() {
    let cell = source();
    let mut view = TranscriptView::default();
    view.set_presentation(false, HistoryRenderMode::Intent);
    let area = Rect::new(0, 0, 80, 12);
    let mut buffer = Buffer::empty(area);
    view.render_parked(area, &mut buffer, &[], Some(&cell), &[], true);
    let content = view.base_layout(&[], 0).unwrap();
    assert!(!content.text().contains("hidden_command"));
    assert!(!content.text().contains("hidden_output"));
}

#[test]
fn intent_search_keeps_collapsed_tool_sources_hidden() {
    let cells: Vec<Arc<dyn HistoryCell>> = vec![Arc::new(source())];
    let mut view = TranscriptView::default();
    view.set_presentation(false, HistoryRenderMode::Intent);
    tests::render(&mut view, &cells, 80, 12);
    view.begin_search();
    let anchor = Anchor {
        key: view.entry_key(&cells, 0),
        index: 0,
        offset: 0,
        row_bias: 0,
    };
    let layout = view.search_layout(&cells, anchor).unwrap();
    assert!(!layout.text().contains("hidden_command"));
    assert!(!layout.text().contains("hidden_output"));
    view.set_presentation(true, HistoryRenderMode::Intent);
    tests::render(&mut view, &cells, 80, 12);
    let layout = view.search_layout(&cells, anchor).unwrap();
    assert!(layout.text().contains("hidden_command"));
    assert!(layout.text().contains("hidden_output"));
}

#[test]
fn intent_explicit_full_records_open_on_first_request() {
    // A newly opened transcript overlay starts in Rich mode. Its explicit request
    // for full records must be honored even when the conversation uses Intent.
    let cells: Vec<Arc<dyn HistoryCell>> = vec![Arc::new(source())];
    let mut view = TranscriptView::default();
    view.set_presentation(true, HistoryRenderMode::Intent);
    tests::render(&mut view, &cells, 80, 12);
    assert!(view.is_detailed());
    let layout = view.base_layout(&cells, 0).unwrap();
    assert!(layout.text().contains("hidden_command"));
    assert!(layout.text().contains("hidden_output"));
}

#[test]
fn intent_detail_click_uses_wrapped_unicode_layout_and_ignores_assistant_lookalikes() {
    let cells: Vec<Arc<dyn HistoryCell>> = vec![
        Arc::new(source()),
        Arc::new(crate::history_cell::PlainHistoryCell::new(vec![
            Line::from("[查看详情]"),
        ])),
    ];
    for width in [12, 24, 80] {
        let mut view = TranscriptView::default();
        view.set_presentation(false, HistoryRenderMode::Intent);
        let area = Rect::new(3, 4, width, 20);
        view.render(area, &mut Buffer::empty(area), &cells);
        let hits: Vec<_> = (area.y..area.bottom())
            .flat_map(|y| (area.x..area.right()).map(move |x| (x, y)))
            .filter_map(|(x, y)| view.intent_details_at(x, y, &cells))
            .collect();
        assert!(!hits.is_empty(), "no clickable label at width {width}");
        assert!(hits.iter().all(|index| *index == 0));
        view.set_presentation(true, HistoryRenderMode::Intent);
        view.render(area, &mut Buffer::empty(area), &cells);
        assert!((area.y..area.bottom()).all(|y| {
            (area.x..area.right()).all(|x| view.intent_details_at(x, y, &cells).is_none())
        }));
    }
}
