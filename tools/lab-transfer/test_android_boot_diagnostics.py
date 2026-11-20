"""Only known system log counts may appear in the lab report."""

import unittest

from android_boot_diagnostics import can_retry_with_veth, collect_boot_log, docker_start_failure, summarize_boot_log


class AndroidBootDiagnosticsTests(unittest.TestCase):
    def test_classifies_system_watchdog_without_exporting_reason(self):
        result = summarize_boot_log(
            "W/Watchdog ( 171): *** WATCHDOG KILLING SYSTEM PROCESS: sensitive detail\n"
            "I/Watchdog ( 171): WAITED_UNTIL_PRE_WATCHDOG\n"
            "W/WindowManager( 171): ***** BOOT TIMEOUT: forcing display enabled\n"
            "W/WindowManager( 171): performEnableScreen: bootFinished() failed.\n"
            "W/Unexpected( 270): *** WATCHDOG KILLING SYSTEM PROCESS: other detail")
        self.assertEqual(result["system_server_watchdog_kill"], 1)
        self.assertEqual(result["system_server_pre_watchdog"], 1)
        self.assertEqual(result["window_boot_timeout"], 1)
        self.assertEqual(result["surface_boot_finished_failure"], 1)
        self.assertNotIn("detail", str(result))

    def test_partial_log_is_explicitly_incomplete(self):
        result = collect_boot_log(["docker"], "cid", lambda *_: ("not safe to print", 137, True, False))
        self.assertFalse(result["read_complete"])
        self.assertTrue(result["timed_out"])
        self.assertNotIn("not safe to print", str(result))

    def test_docker_bind_failure_keeps_only_engine_labels_and_lab_target(self):
        result = docker_start_failure(
            'failed to create shim task: OCI runtime create failed: error mounting '
            '"/unique-private-runner/source" to rootfs at "/dev/binder": no such file or directory',
            1, ["/data", "/dev/binder", "/dev/hwbinder"])
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["error_categories"], ["oci_runtime", "mount_setup", "mount_path_missing"])
        self.assertEqual(result["mentioned_lab_mounts"], ["/dev/binder"])
        self.assertNotIn("unique-private-runner", str(result))

    def test_longer_path_does_not_match_lab_mount(self):
        result = docker_start_failure('error mounting "/data/private-path"', 1, ["/data"])
        self.assertEqual(result["mentioned_lab_mounts"], [])

    def test_veth_recovery_requires_the_matching_engine_and_host_state(self):
        veth_error = docker_start_failure(
            "failed to create endpoint: failed to add the host pair interfaces: operation not supported", 1, [])
        self.assertTrue(can_retry_with_veth(veth_error, {"veth_present": False, "kernel_modprobe_setting": "empty"}))
        self.assertFalse(can_retry_with_veth(veth_error, {"veth_present": True, "kernel_modprobe_setting": "empty"}))
        self.assertFalse(can_retry_with_veth(veth_error, {"veth_present": False, "kernel_modprobe_setting": "set"}))
        mount_error = docker_start_failure("error mounting: operation not supported", 1, [])
        self.assertFalse(can_retry_with_veth(mount_error, {"veth_present": False, "kernel_modprobe_setting": "empty"}))


if __name__ == "__main__":
    unittest.main()
