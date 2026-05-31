"""
KWin-specific fixes for virtual display management.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
from pathlib import Path
from typing import Any


def _session_users() -> list[tuple[str, int, Path]]:
    users: list[tuple[str, int, Path]] = []
    run_user = Path("/run/user")
    if not run_user.exists():
        return users

    for user_dir in sorted(run_user.iterdir()):
        if not user_dir.name.isdigit() or not (user_dir / "bus").exists():
            continue
        uid = int(user_dir.name)
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            continue
        users.append((user, uid, user_dir))

    return users


def disable_kwin_output(port: str) -> bool:
    """Ask KScreen/KWin to disable an output in the active user session."""
    for user, uid, runtime_dir in _session_users():
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir / 'bus'}"
        try:
            result = subprocess.run(
                ["runuser", "-u", user, "--", "kscreen-doctor", f"output.{port}.disable"],
                capture_output=True,
                text=True,
                env=env,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            print(f"  ✓ Disabled {port} in KScreen session for uid {uid}")
            return True

    return False


def clear_kwin_output_config(port: str) -> None:
    """
    Remove any stale KWin saved output config for *port* so that KWin applies
    the EDID preferred mode instead of a previously-saved resolution/scale.

    KWin stores per-connector config keyed by connector name in
    ~/.config/kwinoutputconfig.json.  When a physical monitor was last seen on
    e.g. DP-2 at 2560x1440, that entry persists and overrides our custom EDID
    when the virtual connector appears on the same port name.

    Runs as root (via sudo), so we look up the real user from $SUDO_USER.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        return

    try:
        home = Path(pwd.getpwnam(sudo_user).pw_dir)
    except KeyError:
        return

    config_path = home / ".config" / "kwinoutputconfig.json"
    if not config_path.exists():
        return

    try:
        data: Any = json.loads(config_path.read_text())
        # kwinoutputconfig.json may be {"outputs": [...]} or a bare [...]
        if isinstance(data, list):
            outputs: list[Any] = data
            filtered: list[Any] = [o for o in outputs if o.get("name") != port]
            if len(filtered) < len(outputs):
                _ = config_path.write_text(json.dumps(filtered, indent=2))
                print(f"  ✓ Cleared KWin saved config for {port} (was overriding EDID resolution)")
            else:
                print(f"  ✓ No stale KWin config for {port}")
        else:
            outputs = data.get("outputs", [])
            original_count: int = len(outputs)
            data["outputs"] = [o for o in outputs if o.get("name") != port]
            if len(data["outputs"]) < original_count:
                _ = config_path.write_text(json.dumps(data, indent=2))
                print(f"  ✓ Cleared KWin saved config for {port} (was overriding EDID resolution)")
            else:
                print(f"  ✓ No stale KWin config for {port}")
    except Exception as e:
        print(f"  Warning: Could not update kwinoutputconfig.json: {e}")
