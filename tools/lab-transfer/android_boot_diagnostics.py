"""Summarize failures of the disposable Android system before the APK install."""

import re


LOG_TAGS = ("Watchdog:I", "WindowManager:W", "AndroidRuntime:E", "libEGL:W", "libc:F", "*:S")
LOG_TAIL_LIMIT = 1024
LOG_LINE = re.compile(r"^[VDIWEFS]/([A-Za-z]+)\s*\(\s*[0-9]+\):\s*(.*)$")


def summarize_boot_log(text):
    counts = dict.fromkeys(("system_server_watchdog_kill", "system_server_pre_watchdog",
                            "window_boot_timeout", "surface_boot_finished_failure",
                            "system_process_fatal_exception", "egl_error", "surfaceflinger_fatal_signal"), 0)
    for line in text.splitlines():
        match = LOG_LINE.fullmatch(line)
        if not match:
            continue
        tag, message = match.groups()
        if tag == "Watchdog" and message.startswith("*** WATCHDOG KILLING SYSTEM PROCESS:"):
            counts["system_server_watchdog_kill"] += 1
        elif tag == "Watchdog" and message == "WAITED_UNTIL_PRE_WATCHDOG":
            counts["system_server_pre_watchdog"] += 1
        elif tag == "WindowManager" and message.startswith("***** BOOT TIMEOUT: forcing display enabled"):
            counts["window_boot_timeout"] += 1
        elif tag == "WindowManager" and message.startswith("performEnableScreen: bootFinished() failed."):
            counts["surface_boot_finished_failure"] += 1
        elif tag == "AndroidRuntime" and message.startswith("*** FATAL EXCEPTION IN SYSTEM PROCESS:"):
            counts["system_process_fatal_exception"] += 1
        elif tag == "libEGL" and message.startswith("EGL error"):
            counts["egl_error"] += 1
        elif tag == "libc" and re.fullmatch(r"Fatal signal [0-9]+ .*\(surfaceflinger\).*", message):
            counts["surfaceflinger_fatal_signal"] += 1
    return counts


def collect_boot_log(docker, cid, capture):
    output, code, timed_out, capped = capture(
        docker + ["exec", cid, "/system/bin/logcat", "-b", "main", "-b", "system", "-b", "crash",
                  "-t", str(LOG_TAIL_LIMIT), "-v", "brief", "-s", *LOG_TAGS], 12)
    return {"scope": "Selected system log categories before APK installation",
            "max_tail_log_entries": LOG_TAIL_LIMIT,
            "read_complete": code == 0 and not timed_out and not capped,
            "exit_code": code, "timed_out": timed_out, "output_limit_reached": capped,
            "counts": summarize_boot_log(output)}
