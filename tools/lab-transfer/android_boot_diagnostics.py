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


def docker_start_failure(stderr, exit_code, mount_targets):
    """Keep only known engine error labels and exact lab mount targets."""
    message = stderr.lower()
    patterns = {
        "oci_runtime": ("oci runtime", "failed to create shim task"),
        "mount_setup": ("error mounting", "failed to mount", "mount through procfd", "mount src="),
        "mount_path_missing": ("no such file or directory", "path does not exist"),
        "mount_type_mismatch": ("not a directory", "not a file"),
        "resource_busy": ("device or resource busy", "resource temporarily unavailable"),
        "permission_denied": ("permission denied", "operation not permitted"),
        "read_only_filesystem": ("read-only file system",),
        "invalid_argument": ("invalid argument",),
        "duplicate_resource": ("already exists", "file exists", "duplicate mount"),
        "network_setup": ("network sandbox", "failed to create endpoint", "failed to set up container networking"),
        "veth_pair_setup": ("veth pair", "pair interfaces"),
        "kernel_operation_unavailable": ("operation not supported", "no such device"),
        "device_setup": ("create device", "device cgroup", "error gathering device information"),
        "exec_failure": ("exec: ", "executable file not found"),
        "no_space_left": ("no space left",),
    }
    return {"exit_code": exit_code, "stderr_present": bool(stderr),
            "error_categories": [name for name, phrases in patterns.items() if any(phrase in message for phrase in phrases)],
            "mentioned_lab_mounts": [target for target in mount_targets
                                     if re.search(fr"{re.escape(target)}(?![\w./-])", stderr)]}


def can_retry_with_veth(failure, network_state):
    categories = set(failure["error_categories"])
    return ("veth_pair_setup" in categories and "kernel_operation_unavailable" in categories
            and not network_state["veth_present"] and network_state["kernel_modprobe_setting"] == "empty")
