"""Identify owned processes without reading other processes' arguments or environment."""
import os
from pathlib import Path


def scoped(root, tracked=()):
    from managed_runtime import read
    root = Path(root).resolve(strict=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    rows, owned = {}, set()
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            text = (folder / 'stat').read_text()
            tail = text[text.rfind(')') + 2:].split()
            if tail[0] == 'Z':
                continue
            pid = int(folder.name)
            name = text[text.find('(') + 1:text.rfind(')')]
            row = dict(ProcessId=pid, ParentProcessId=int(tail[1]), Name=name,
                       ExecutablePath=None, StartTicks=int(tail[19]), BootId=boot)
            rows[pid] = row
            try:
                executable = Path(os.readlink(folder / 'exe'))
                if executable.is_relative_to(root):
                    owned.add(pid)
                    row.update(Name=executable.name, ExecutablePath=str(executable))
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                pass  # Hardened Codex processes deny exe access even to the same user.
        except (FileNotFoundError, ProcessLookupError):
            pass
    # Native daemon records retain boot ID and start ticks across process hardening. Stale
    # records must never select an unrelated process after a reboot or PID reuse.
    records = []
    for path in root.glob('d/*/app-server-daemon/*.pid'):
        record = read(path)
        identity = record.get('processIdentity', record.get('linuxProcessIdentity'))
        if identity:
            records.append((record['pid'], identity.get('bootId'), identity.get('startTicks')))
        elif record.get('pid') in rows:
            raise RuntimeError('Cannot prove a legacy daemon PID identity; stop it using its own daemon command first.')
    records.extend((row['ProcessId'], row['BootId'], row['StartTicks']) for row in tracked)
    for pid, record_boot, ticks in records:
        if pid in rows and record_boot == boot and rows[pid]['StartTicks'] == ticks:
            owned.add(pid)
    while True:
        children = {pid for pid, row in rows.items() if row['ParentProcessId'] in owned}
        if children.issubset(owned):
            break
        owned.update(children)
    return [rows[pid] for pid in sorted(owned)]
