"""Shared allowlist for client and receiver; no desktop dependencies."""
import re

EVENTS = frozenset({"startup", "startup_failed", "agent_failed", "tray_failed", "tray_ready",
                    "tray_update_failed", "window_hidden_in_tray", "window_minimized_without_tray",
                    "tray_notification_failed", "ui_callback_failed", "remote_operation_failed",
                    "file_transfer_failed", "diagnostic_browser_failed"})
ERRORS = frozenset(name for name in EVENTS if name.endswith("_failed"))


def clean_record(record):
    """Allowlist both fields and values; do not forward free-form log text."""
    name = record.get("event")
    if name not in EVENTS:
        return None
    result = {"event": name}
    error = record.get("error_type")
    if isinstance(error, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", error):
        result["error_type"] = error
    frames = []
    for frame in record.get("frames", [])[-6:]:
        if not isinstance(frame, dict):
            continue
        file, function, line = frame.get("file"), frame.get("function"), frame.get("line")
        if (isinstance(file, str) and re.fullmatch(r"[A-Za-z_0-9.-]{1,80}\.py", file)
                and isinstance(function, str) and re.fullmatch(r"[A-Za-z_0-9<>]{1,80}", function)
                and type(line) is int and 0 < line < 100000):
            frames.append({"file": file, "function": function, "line": line})
    if frames:
        result["frames"] = frames
    return result


