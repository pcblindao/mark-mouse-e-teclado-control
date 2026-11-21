"""Shared allowlist for client and receiver; no desktop dependencies."""
import re

EVENTS = frozenset({"startup", "startup_failed", "agent_failed", "tray_failed", "tray_ready",
                    "tray_update_failed", "window_hidden_in_tray", "window_minimized_without_tray",
                    "tray_notification_failed", "ui_callback_failed", "remote_operation_failed",
                    "file_transfer_failed", "diagnostic_browser_failed", "viewer_failed", "capture_failed", "session_failed", "input_release_failed", "autostart_failed", "config_failed",
                    "update_check", "update_verified", "update_failed", "update_applied", "update_rollback",
                    "session_started", "session_ended", "viewer_connected", "viewer_stats", "stream_stats"})
ERRORS = frozenset(name for name in EVENTS if name.endswith("_failed"))
ACTIVITY = frozenset({"session_started", "session_ended", "viewer_connected", "viewer_stats", "stream_stats",
                      "window_hidden_in_tray", "window_minimized_without_tray", "update_applied", "update_rollback"})
METRICS = {"width", "height", "fps", "decode_ms", "capture_ms", "send_ms", "rtt_ms", "frames", "bytes"}


def clean_record(record):
    """Allowlist both fields and values; do not forward free-form log text."""
    if not isinstance(record, dict):
        return None
    name = record.get("event")
    if not isinstance(name, str) or name not in EVENTS:
        return None
    result = {"event": name}
    error = record.get("error_type")
    if isinstance(error, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", error):
        result["error_type"] = error
    frames = []
    candidates = record.get("frames", [])
    for frame in (candidates[-6:] if isinstance(candidates, list) else []):
        if not isinstance(frame, dict):
            continue
        file, function, line = frame.get("file"), frame.get("function"), frame.get("line")
        if (isinstance(file, str) and re.fullmatch(r"[A-Za-z_0-9.-]{1,80}\.py", file)
                and isinstance(function, str) and re.fullmatch(r"[A-Za-z_0-9<>]{1,80}", function)
                and type(line) is int and 0 < line < 100000):
            frames.append({"file": file, "function": function, "line": line})
    if frames:
        result["frames"] = frames
    metrics = record.get("metrics", {})
    if isinstance(metrics, dict):
        allowed = {key: value for key, value in metrics.items()
                   if key in METRICS and type(value) is int and 0 <= value <= 1_000_000_000}
        if allowed:
            result["metrics"] = allowed
    return result


