"""Native PTY transport and equivalent shell fixtures for the same acceptance assertions."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_support import WINDOWS, executable

if WINDOWS:
    from winpty import PtyProcess
else:
    from ptyprocess import PtyProcessUnicode

    class PtyProcess:
        @staticmethod
        def spawn(args, **kwargs):
            kwargs.pop('backend', None)  # ConPTY backend selection is Windows-only.
            return PtyProcessUnicode.spawn(args, **kwargs)


def long_command(*, started=True, failed=False):
    if WINDOWS:
        start = "Set-Content -LiteralPath 'started-command' -Value 'started'; " if started else ''
        return (start + "Write-Output 'RAW_STDOUT_MARKER'; "
                "1..1000 | ForEach-Object { Write-Output ('RAW_STDOUT_MARKER_' + $_) }; "
                "Write-Error 'RAW_STDERR_MARKER' -ErrorAction Continue; "
                "while (-not (Test-Path -LiteralPath 'release-command')) { Start-Sleep -Milliseconds 100 }; "
                "Write-Output 'RAW_FINISHED_MARKER'" + ('; exit 7' if failed else '') +
                ' # RAW_CMD_MARKER ' + 'long-command-content ' * 100)
    start = "printf started > started-command; " if started else ''
    return (start + "printf 'RAW_STDOUT_MARKER\\n'; "
            "for n in $(seq 1 1000); do printf 'RAW_STDOUT_MARKER_%s\\n' \"$n\"; done; "
            "printf 'RAW_STDERR_MARKER\\n' >&2; "
            "while [ ! -f release-command ]; do sleep 0.1; done; "
            "printf 'RAW_FINISHED_MARKER\\n'" + ('; exit 7' if failed else '') +
            ' # RAW_CMD_MARKER ' + 'long-command-content ' * 100)


def task_command(name):
    if WINDOWS:
        return (f"Set-Content -LiteralPath 'started-{name}' -Value 'started'; "
                f"Write-Output 'RAW_V2_STDOUT_{name}'; Write-Error 'RAW_V2_STDERR_{name}' -ErrorAction Continue; "
                f"while (-not (Test-Path -LiteralPath 'release-{name}')) {{ Start-Sleep -Milliseconds 100 }}; "
                f"Write-Output 'RAW_V2_DONE_{name}' # RAW_V2_COMMAND " + 'long-script ' * 100)
    return (f"printf started > started-{name}; printf 'RAW_V2_STDOUT_{name}\\n'; "
            f"printf 'RAW_V2_STDERR_{name}\\n' >&2; "
            f"while [ ! -f release-{name} ]; do sleep 0.1; done; "
            f"printf 'RAW_V2_DONE_{name}\\n' # RAW_V2_COMMAND " + 'long-script ' * 100)
