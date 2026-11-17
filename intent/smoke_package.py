"""Exercise assembled native binaries with isolated data, without a model account.

This is a binary smoke check, not a replacement for terminal or model scenarios.
"""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from platform_support import executable


REPO = Path(__file__).resolve().parent.parent


def initialize_rpc(command, params, environment, run, name):
    messages = queue.Queue()
    with (run / (name + ".stderr.log")).open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(command, cwd=run, env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")

        def read_messages():
            try:
                for line in process.stdout:
                    messages.put(json.loads(line))
            except Exception as error:
                messages.put(error)
            finally:
                messages.put(EOFError("RPC process closed its output"))

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()
        try:
            process.stdin.write(json.dumps({"id": 1, "method": "initialize", "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 60
            received = []
            while True:
                response = messages.get(timeout=max(0, deadline - time.monotonic()))
                if isinstance(response, Exception):
                    raise response
                received.append(response)
                if response.get("id") == 1:
                    break
                if len(received) >= 1000:
                    raise RuntimeError("RPC initialization response missing from incoming messages")
            (run / (name + ".response.json")).write_text(json.dumps(received, indent=2), encoding="utf-8")
            if response.get("id") != 1 or "result" not in response:
                raise RuntimeError(f"{name} initialization failed: {response}")
            process.stdin.write('{"method":"initialized","params":{}}\n')
            process.stdin.flush()
            return response["result"]
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=15)
            reader.join(timeout=5)
            process.stdout.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    manifest = json.loads((package / "codex-package.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if "+intent." not in version:
        raise RuntimeError("Expected a codex-intent candidate package")
    run = REPO / ".intent-work" / ("binary-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    home = run / "codex-home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'check_for_update_on_startup = false\ncli_auth_credentials_store = "file"\n'
        '[tui]\nintent_view = true\n[analytics]\nenabled = false\n[feedback]\nenabled = false\n',
        encoding="utf-8",
    )
    environment = {**os.environ, "CODEX_HOME": str(home)}
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        environment.pop(name, None)
    cli = package / "bin" / executable("codex-intent")
    report = {"status": "running", "scope": "binary-smoke-only", "version": version,
              "package": str(package), "codex_home": str(home), "checks": []}

    def check(name, command, expected, success=True):
        result = subprocess.run([str(part) for part in command], cwd=run, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        output = result.stdout + result.stderr
        (run / (name + ".log")).write_text(output, encoding="utf-8")
        if (result.returncode == 0) != success or expected not in output:
            raise RuntimeError(f"{name} failed with exit code {result.returncode}; see log")
        report["checks"].append({"name": name, "exit_code": result.returncode, "passed": True})
        print("PASS: " + name, flush=True)

    try:
        check("cli-version", [cli, "--version"], "codex-intent " + version)
        check("cli-help", [cli, "--help"], "codex-intent")
        check("cli-features", [cli, "features", "list"], "code_mode")
        guidance = "Maintain-Intent.ps1" if os.name == 'nt' else "maintain-intent"
        check("official-update-blocked", [cli, "update"], guidance, success=False)
        check("daemon-update-blocked", [cli, "app-server", "daemon", "update"], guidance, success=False)
        check("daemon-from-cli-blocked", [cli, "app-server", "daemon", "update", "--from-cli", "--yes"], guidance, success=False)
        check("executor-version", [package / "bin" / executable("codex-exec-server"), "--version"], version)
        check("code-host-help", [package / "bin" / executable("codex-code-mode-host"), "--help"], "--listen")
        check("ripgrep", [package / "codex-path" / executable("rg"), "--version"], "ripgrep")
        result = initialize_rpc([str(package / "bin" / executable("codex-exec-server")), "--listen", "stdio"],
                                {"clientName": "intent-binary-smoke"}, environment, run, "executor")
        if not result.get("sessionId") or result["environmentInfo"]["executorVersion"] != version:
            raise RuntimeError("Executor initialization has missing session or mismatched package version")
        report["checks"].append({"name": "executor-initialize", "passed": True})
        result = initialize_rpc([str(cli), "app-server", "--strict-config", "--listen", "stdio://"],
                                {"clientInfo": {"name": "intent-binary-smoke", "version": "1"},
                                 "capabilities": {"experimentalApi": True}}, environment, run, "app-server")
        if Path(result["codexHome"]).resolve() != home.resolve():
            raise RuntimeError("App server did not use isolated CODEX_HOME")
        if "/" + version + " " not in result["userAgent"]:
            raise RuntimeError("App server reported an uncustomized or mismatched version")
        report["checks"].append({"name": "app-server-isolated-initialize", "passed": True})
        report["status"] = "passed-binary-smoke-only"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Binary smoke report: " + str(run / "report.json"), flush=True)


if __name__ == "__main__":
    main()
