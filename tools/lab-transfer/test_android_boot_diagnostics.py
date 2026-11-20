"""Only known system log counts may appear in the lab report."""

import unittest

from android_boot_diagnostics import collect_boot_log, summarize_boot_log


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


if __name__ == "__main__":
    unittest.main()
