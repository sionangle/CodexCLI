//! Maintenance-only, idle-checked, non-forcing daemon drain.

use crate::Daemon;
use crate::LifecycleOutput;
use crate::LifecycleStatus;
use crate::backend;
use crate::client;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use codex_app_server_protocol::JSONRPCMessage;
use codex_app_server_protocol::JSONRPCNotification;
use codex_app_server_protocol::JSONRPCRequest;
use codex_app_server_protocol::RequestId;
use codex_app_server_protocol::ThreadLoadedListResponse;
use codex_app_server_protocol::ThreadReadResponse;
use codex_app_server_protocol::ThreadStatus;
use codex_uds::UnixStream;
use serde::de::DeserializeOwned;
use serde_json::json;
use std::collections::HashSet;
use std::path::Path;
use std::time::Duration;
use tokio_tungstenite::WebSocketStream;

async fn request<T: DeserializeOwned>(
    socket: &mut WebSocketStream<UnixStream>,
    id: i64,
    method: &str,
    params: serde_json::Value,
) -> Result<T> {
    let request_id = RequestId::Integer(id);
    client::send_message(
        socket,
        &JSONRPCMessage::Request(JSONRPCRequest {
            id: request_id.clone(),
            method: method.into(),
            params: Some(params),
            trace: None,
        }),
    )
    .await?;
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match client::read_message(socket).await? {
                JSONRPCMessage::Response(response) if response.id == request_id => {
                    return Ok(serde_json::from_value(response.result)?);
                }
                JSONRPCMessage::Error(error) if error.id == request_id => {
                    anyhow::bail!("daemon refused maintenance state inspection: {method}");
                }
                _ => (),
            }
        }
    })
    .await
    .context("daemon state inspection timed out; no stop was requested")?
}

fn idle(status: &ThreadStatus) -> bool {
    matches!(status, ThreadStatus::Idle | ThreadStatus::NotLoaded)
}

async fn ensure_idle(path: &Path) -> Result<()> {
    let mut socket = client::connect(path).await?;
    client::initialize(&mut socket, true).await?;
    client::send_message(
        &mut socket,
        &JSONRPCMessage::Notification(JSONRPCNotification {
            method: "initialized".into(),
            params: None,
        }),
    )
    .await?;
    let mut id = 2;
    let mut cursor: Option<String> = None;
    let mut cursors = HashSet::new();
    let mut inspected = 0;
    loop {
        let page: ThreadLoadedListResponse = request(
            &mut socket,
            id,
            "thread/loaded/list",
            json!({"cursor": cursor, "limit": 100}),
        )
        .await?;
        id += 1;
        for thread in page.data {
            inspected += 1;
            ensure!(
                inspected <= 1_000,
                "too many loaded threads to verify safely"
            );
            let response: ThreadReadResponse = request(
                &mut socket,
                id,
                "thread/read",
                json!({"threadId": thread, "includeTurns": false}),
            )
            .await?;
            id += 1;
            ensure!(
                idle(&response.thread.status),
                "a task is active or its state is uncertain; no stop was requested"
            );
        }
        let Some(next) = page.next_cursor else {
            break;
        };
        ensure!(
            cursors.insert(next.clone()),
            "daemon pagination did not advance"
        );
        cursor = Some(next);
    }
    socket.close(None).await?;
    Ok(())
}

/// Unlike ordinary daemon stop, this operation never uses a forced-exit deadline.
#[cfg(any(windows, target_os = "linux"))]
pub async fn quiesce_idle() -> Result<LifecycleOutput> {
    crate::ensure_supported_platform()?;
    let owner = Daemon::from_environment()?;
    let _lock = owner.acquire_operation_lock().await?;
    let daemon = owner.current_installation()?;
    let settings = daemon.load_settings().await?;
    ensure!(
        !settings.remote_control_enabled,
        "remote control must be disabled before maintenance"
    );
    ensure!(
        !backend::pid_update_loop_backend(daemon.backend_paths(&settings))
            .is_starting_or_running()
            .await?,
        "an updater is active; maintenance cannot establish exclusive ownership"
    );
    let Some(backend) = daemon.running_backend_instance(&settings).await? else {
        ensure!(
            client::probe(&daemon.socket_path).await.is_err(),
            "an unmanaged daemon is running"
        );
        return Ok(daemon
            .output(LifecycleStatus::NotRunning, None, None, None)
            .await);
    };
    let version = client::probe(&daemon.socket_path).await?.app_server_version;
    let revision = version
        .split_once("+intent.")
        .and_then(|(_, value)| value.parse::<u32>().ok());
    ensure!(
        revision.is_some_and(|revision| revision >= 3),
        "this older daemon lacks the non-forcing maintenance drain; keep the existing version until it has been stopped safely"
    );
    ensure_idle(&daemon.socket_path).await?;
    backend.intent_graceful_stop().await?;
    crate::thread_recovery::discard_pending(&daemon)?;
    Ok(daemon
        .output(
            LifecycleStatus::Stopped,
            Some(backend::BackendKind::Pid),
            None,
            None,
        )
        .await)
}

#[cfg(not(any(windows, target_os = "linux")))]
pub async fn quiesce_idle() -> Result<LifecycleOutput> {
    anyhow::bail!("codex-intent maintenance supports Windows and Linux")
}

#[cfg(test)]
mod tests {
    use super::*;
    use codex_app_server_protocol::ThreadActiveFlag;

    #[test]
    fn intent_maintenance_refuses_active_approval_and_uncertain_threads() {
        assert!(idle(&ThreadStatus::Idle));
        assert!(idle(&ThreadStatus::NotLoaded));
        assert!(!idle(&ThreadStatus::Active {
            active_flags: vec![]
        }));
        assert!(!idle(&ThreadStatus::Active {
            active_flags: vec![ThreadActiveFlag::WaitingOnApproval]
        }));
        assert!(!idle(&ThreadStatus::SystemError));
    }
}
