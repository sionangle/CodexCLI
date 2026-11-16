use super::*;
use crate::exec_cell::ExecCell;
use crate::exec_cell::new_active_exec_command;
use codex_app_server_protocol::CommandExecutionSource;
use codex_app_server_protocol::DynamicToolCallOutputContentItem;
use codex_app_server_protocol::DynamicToolCallStatus;
use codex_app_server_protocol::ThreadItemPayload;

const COMMAND: &str = "PRIVATE_COMMAND_SENTINEL";
const OUTPUT: &str = "PRIVATE_OUTPUT_SENTINEL";

fn text(lines: Vec<HyperlinkLine>) -> String {
    visible_lines(lines)
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}

fn command() -> ExecCell {
    new_active_exec_command(
        "intent-command".into(),
        vec!["powershell".into(), "-Command".into(), COMMAND.repeat(200)],
        Vec::new(),
        CommandExecutionSource::Agent,
        None,
        false,
    )
}

#[test]
fn intent_hides_streamed_and_completed_command_without_discarding_source() {
    let mut cell = command();
    assert!(cell.append_output("intent-command", &format!("{OUTPUT}\n").repeat(10_000)));
    for width in [12, 40, 120] {
        let rendered =
            text(cell.display_hyperlink_lines_for_mode(width, HistoryRenderMode::Intent));
        assert!(!rendered.contains(COMMAND));
        assert!(!rendered.contains(OUTPUT));
        assert!(rendered.contains("正在执行"));
    }
    assert!(cell.complete_call(
        "intent-command",
        CommandOutput::new(23, format!("{OUTPUT}\n").repeat(10_000)),
        Duration::from_secs(2),
    ));
    let hidden = text(cell.intent_hyperlink_lines(80));
    assert!(hidden.contains("23"));
    assert!(!hidden.contains(OUTPUT));
    let detail = text(cell.expanded_hyperlink_lines(120));
    assert!(detail.contains(COMMAND));
    assert_eq!(detail.matches(OUTPUT).count(), 10_000);
    assert!(
        cell.raw_lines()
            .iter()
            .any(|line| line.to_string().contains(COMMAND))
    );
}

#[test]
fn intent_hides_dynamic_json_and_failure_output() {
    let cell = DynamicToolCallCell::from_item(ThreadItemPayload::DynamicToolCall {
        id: "dynamic-intent".into(),
        namespace: Some("functions".into()),
        tool: "exec".into(),
        arguments: serde_json::json!({"script": COMMAND.repeat(200)}),
        status: DynamicToolCallStatus::Failed,
        content_items: Some(vec![DynamicToolCallOutputContentItem::InputText {
            text: OUTPUT.repeat(200),
        }]),
        success: Some(false),
        duration_ms: Some(3),
    })
    .expect("dynamic call");
    let hidden = text(cell.intent_hyperlink_lines(80));
    assert!(hidden.contains("失败"));
    assert!(!hidden.contains(COMMAND));
    assert!(!hidden.contains(OUTPUT));
    let raw = cell
        .raw_lines()
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(raw.contains(COMMAND));
    assert!(raw.contains(OUTPUT));
}

#[test]
fn intent_preserves_changes_approval_and_actionable_error() {
    let cwd = std::env::current_dir().expect("cwd");
    let patch = new_patch_event(
        HashMap::from([(
            cwd.join("intent.rs"),
            FileChange::Add {
                content: "fn visible_change() {}\n".into(),
            },
        )]),
        &cwd,
    );
    let approval = new_approval_decision_cell(
        ApprovalDecisionSubject::Command(vec!["git".into(), "status".into()]),
        ReviewDecision::Denied,
        ApprovalDecisionActor::User,
    );
    let error = new_error_event("Permission denied: choose a writable directory.".into());
    for cell in [&patch as &dyn HistoryCell, approval.as_ref(), &error] {
        let hidden = text(cell.display_hyperlink_lines_for_mode(100, HistoryRenderMode::Intent));
        let rich = text(cell.display_hyperlink_lines_for_mode(100, HistoryRenderMode::Rich));
        assert_eq!(hidden, rich);
        assert!(!hidden.is_empty());
    }
    assert!(text(patch.intent_hyperlink_lines(100)).contains("visible_change"));
}

#[test]
fn intent_background_interactions_retain_explicit_details() {
    let cell = new_unified_exec_interaction(Some(COMMAND.into()), OUTPUT.into());
    assert!(cell.intent_hyperlink_lines(80).is_empty());
    let detail = text(cell.expanded_hyperlink_lines(120));
    assert!(detail.contains(COMMAND));
    assert!(detail.contains(OUTPUT));
}
