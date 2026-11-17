//! Compact task rows record their painted name bounds for mouse activation.
use std::cell::RefCell;

use crate::render::renderable::Renderable;
use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Stylize;
use ratatui::text::Line;
use ratatui::widgets::Widget;

pub(crate) struct TaskRow {
    pub(crate) id: String,
    pub(crate) title: String,
    pub(crate) elapsed: String,
    pub(crate) depth: usize,
}

#[derive(Default)]
pub(crate) struct IntentTaskRows {
    pub(crate) rows: Vec<TaskRow>,
    pub(crate) overflow: usize,
    painted: RefCell<Vec<(Rect, String)>>,
}

impl IntentTaskRows {
    pub(crate) fn is_empty(&self) -> bool {
        self.rows.is_empty() && self.overflow == 0
    }

    pub(crate) fn task_at(&self, x: u16, y: u16) -> Option<String> {
        self.painted.borrow().iter().find_map(|(area, id)| {
            (area.contains((x, y).into()) && self.rows.iter().any(|row| &row.id == id))
                .then(|| id.clone())
        })
    }
}

impl Renderable for IntentTaskRows {
    fn desired_height(&self, _width: u16) -> u16 {
        (self.rows.len() + usize::from(self.overflow > 0)).min(u16::MAX as usize) as u16
    }

    fn render(&self, area: Rect, buf: &mut Buffer) {
        let mut painted = self.painted.borrow_mut();
        painted.clear();
        for (index, row) in self.rows.iter().take(usize::from(area.height)).enumerate() {
            let y = area.y + index as u16;
            let time = Line::from(row.elapsed.clone()).dim();
            let time_width = (time.width() as u16).min(area.width);
            let indent = (2 + row.depth.min(5) * 2) as u16;
            let indent = indent.min(area.width.saturating_sub(time_width));
            let name_area = Rect::new(
                area.x + indent,
                y,
                area.width.saturating_sub(indent + time_width + 2),
                1,
            );
            let name = Line::from(row.title.clone()).cyan().underlined();
            let hit = Rect {
                width: name_area.width.min(name.width() as u16),
                ..name_area
            };
            Widget::render(name, name_area, buf);
            Widget::render(
                time,
                Rect::new(area.right() - time_width, y, time_width, 1),
                buf,
            );
            painted.push((hit, row.id.clone()));
        }
        if self.overflow > 0 && self.rows.len() < usize::from(area.height) {
            Widget::render(
                Line::from(format!("  另有 {} 个子任务 · /subagents", self.overflow)).dim(),
                Rect::new(area.x, area.y + self.rows.len() as u16, area.width, 1),
                buf,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn intent_task_rows_use_one_line_and_actual_name_bounds_after_resize() {
        let mut panel = IntentTaskRows::default();
        panel.rows = vec![
            TaskRow {
                id: "a".into(),
                title: "核实冻结逻辑".into(),
                elapsed: "00:12".into(),
                depth: 0,
            },
            TaskRow {
                id: "b".into(),
                title: "嵌套任务".into(),
                elapsed: "--:--".into(),
                depth: 1,
            },
        ];
        assert_eq!(panel.desired_height(80), 2);
        let area = Rect::new(3, 7, 40, 2);
        let mut buffer = Buffer::empty(area);
        panel.render(area, &mut buffer);
        assert_eq!(panel.task_at(5, 7).as_deref(), Some("a"));
        assert_eq!(panel.task_at(7, 8).as_deref(), Some("b"));
        assert!(panel.task_at(40, 7).is_none());
        let resized = Rect::new(0, 3, 20, 2);
        panel.render(resized, &mut Buffer::empty(resized));
        assert!(panel.task_at(5, 7).is_none());
        assert_eq!(panel.task_at(2, 3).as_deref(), Some("a"));
        panel.rows.clear();
        assert!(panel.task_at(2, 3).is_none());
    }
}
