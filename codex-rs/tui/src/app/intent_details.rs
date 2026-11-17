//! Explicit detail popups leave the conversation's presentation, draft and reading anchor intact.
use super::*;
use crossterm::event::MouseButton;
use crossterm::event::MouseEventKind;

#[derive(Clone)]
pub(super) enum DetailTarget {
    Task(String),
    Cell(Arc<dyn HistoryCell>),
    Live(Vec<String>),
}

impl DetailTarget {
    fn same(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Task(a), Self::Task(b)) => a == b,
            (Self::Cell(a), Self::Cell(b)) => Arc::ptr_eq(a, b),
            (Self::Live(a), Self::Live(b)) => a == b,
            _ => false,
        }
    }
}

pub(super) struct DetailPopup {
    target: DetailTarget,
    owner: Option<ThreadId>,
}

impl App {
    fn intent_detail_target_at(&self, x: u16, y: u16) -> Option<DetailTarget> {
        if let Some(id) = self.chat_widget.intent_task_rows.task_at(x, y) {
            return Some(DetailTarget::Task(id));
        }
        let index = self
            .transcript_view
            .intent_details_at(x, y, &self.transcript_cells)?;
        if let Some(cell) = self.transcript_cells.get(index) {
            return Some(DetailTarget::Cell(Arc::clone(cell)));
        }
        let (ids, lines) = self.chat_widget.intent_live_details();
        (!lines.is_empty()).then_some(DetailTarget::Live(ids))
    }

    fn intent_detail_lines(&self, target: &DetailTarget) -> Option<Vec<Line<'static>>> {
        match target {
            DetailTarget::Task(id) => {
                let task = self.intent_task_panel.tasks.tasks.get(id)?;
                let mut lines = task.lines(crate::intent_tasks::now_ms());
                lines.push(format!("路径：{}", task.path));
                lines.push(format!("线程：{}", task.thread_id));
                Some(
                    lines
                        .into_iter()
                        .flat_map(|text| {
                            text.lines()
                                .map(|line| Line::from(line.to_owned()))
                                .collect::<Vec<_>>()
                        })
                        .collect(),
                )
            }
            DetailTarget::Cell(cell) => Some(cell.raw_lines()),
            DetailTarget::Live(ids) => {
                // A running cell is eventually committed. Keep following that invocation only.
                if let Some(cell) = self
                    .transcript_cells
                    .iter()
                    .rev()
                    .find(|cell| cell.activity_ids().iter().any(|id| ids.contains(id)))
                {
                    return Some(cell.raw_lines());
                }
                let (active, lines) = self.chat_widget.intent_live_details();
                (!ids.is_empty() && active == *ids).then_some(lines)
            }
        }
    }

    pub(super) fn refresh_intent_details(&mut self, tui: &mut tui::Tui) {
        if !matches!(&self.overlay, Some(Overlay::Static(overlay)) if overlay.intent_details) {
            self.intent_task_panel.details = None;
            return;
        }
        let Some(details) = &self.intent_task_panel.details else {
            self.close_transcript_overlay(tui);
            return;
        };
        if details.owner != self.current_displayed_thread_id() {
            self.intent_task_panel.details = None;
            self.close_transcript_overlay(tui);
            return;
        }
        let lines = self.intent_detail_lines(&details.target);
        if let Some(lines) = lines
            && let Some(Overlay::Static(overlay)) = &mut self.overlay
        {
            overlay.update_intent_lines(lines);
        }
    }

    pub(super) fn handle_intent_detail_event(
        &mut self,
        tui: &mut tui::Tui,
        event: &TuiEvent,
    ) -> Result<bool> {
        if self.overlay.is_some() || !self.chat_widget.no_modal_or_popup_active() {
            self.intent_task_panel.pressed = None;
            return Ok(false);
        }
        match event {
            TuiEvent::Mouse(mouse) if mouse.modifiers.is_empty() => match mouse.kind {
                MouseEventKind::Down(MouseButton::Left) => {
                    self.intent_task_panel.pressed = self
                        .intent_detail_target_at(mouse.column, mouse.row)
                        .map(|target| (mouse.column, mouse.row, target));
                }
                MouseEventKind::Up(MouseButton::Left) => {
                    if let Some((x, y, target)) = self.intent_task_panel.pressed.take()
                        && (x, y) == (mouse.column, mouse.row)
                        && self
                            .intent_detail_target_at(x, y)
                            .is_some_and(|current| target.same(&current))
                    {
                        let lines = self
                            .intent_detail_lines(&target)
                            .unwrap_or_else(|| self.chat_widget.intent_live_details().1);
                        let title = if matches!(target, DetailTarget::Task(_)) {
                            "子任务详情 · Esc 返回"
                        } else {
                            "工具详情 · Esc 返回"
                        };
                        self.transcript_view.end_drag();
                        tui.enter_alt_screen()?;
                        let mut overlay = crate::pager_overlay::StaticOverlay::with_title(
                            lines.clone(),
                            title.to_owned(),
                            self.keymap.pager.clone(),
                        );
                        overlay.intent_details = true;
                        overlay.intent_scroll_speed =
                            self.local_settings.tui.mouse_scroll_speed.unwrap_or(1.0);
                        overlay.update_intent_lines(lines);
                        self.intent_task_panel.details = Some(DetailPopup {
                            target,
                            owner: self.current_displayed_thread_id(),
                        });
                        self.overlay = Some(Overlay::Static(overlay));
                        tui.frame_requester().schedule_frame();
                        return Ok(true);
                    }
                }
                MouseEventKind::Moved => (),
                _ => self.intent_task_panel.pressed = None,
            },
            TuiEvent::Draw => (),
            _ => self.intent_task_panel.pressed = None,
        }
        Ok(false)
    }
}
