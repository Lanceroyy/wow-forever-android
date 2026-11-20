"""Adversarial completion checks for the lab's evidence boundary, no device I/O."""
import unittest

import verify_apk_lab_results as subject


def junit(kind):
    lines = []
    for class_name, test_name in sorted(subject.EXPECTED_TESTS[kind]):
        for code in (1, 0):
            lines.extend((f"INSTRUMENTATION_STATUS: class={class_name}",
                          f"INSTRUMENTATION_STATUS: test={test_name}",
                          f"INSTRUMENTATION_STATUS_CODE: {code}"))
    lines.extend((f"OK ({len(subject.EXPECTED_TESTS[kind])} tests)", "INSTRUMENTATION_CODE: -1"))
    return "\n".join(lines) + "\n"


def tls():
    lines = [f"{label} status={status} error={error} certificate_flags={flags}"
             for label, status, error, flags in subject.TLS_MATRIX]
    mappings = ", ".join(f"{name}={value}" for name, value in sorted(subject.TLS_HASHES.items()))
    lines.extend((f"Verified TLS mappings in native PID 123: {{{mappings}}}",
                  "WINE_TLS_PASS", "WINE_TLS_EXTENDED_PASS", "HANDHELD_INSTALLED_WINE_TLS_PASS"))
    return "\n".join(lines)


def d3d():
    return "\n".join((
        "WOW_D3D11_PROBE_V1",
        "DEFAULT attempted=1 hr=0x00000000 feature=0x0000B000 device=1 context=1",
        "EXPLICIT attempted=1 hr=0x00000000 feature=0x0000b000 device=1 context=1",
        "WOW_D3D11_PROBE_RESULT default_fl11=1 adapter_fl11=1", "WOW_D3D11_PROBE_PASS",
        "HANDHELD_D3D11_DEVICE_PROBE_PASS_NOT_GAME_OR_LOADED_PE_ATTESTATION",
    ))


class EvidenceBoundaryTest(unittest.TestCase):
    def test_eight_runner_and_one_separate_drawable_test_pass(self):
        self.assertTrue(subject.summarize("runner", junit("runner"), "")["passed"])
        self.assertTrue(subject.summarize("drawable", junit("drawable"), "")["passed"])

    def test_successful_shell_exit_without_junit_is_rejected(self):
        self.assertFalse(subject.summarize_instrumentation("runner", "", 0)["passed"])

    def test_ok_marker_without_completed_cases_is_rejected(self):
        self.assertFalse(subject.summarize("runner", "OK (8 tests)\nINSTRUMENTATION_CODE: -1\n", "")["passed"])

    def test_assumption_skip_is_rejected(self):
        output = junit("runner").replace("INSTRUMENTATION_STATUS_CODE: 0", "INSTRUMENTATION_STATUS_CODE: -4", 1)
        self.assertFalse(subject.summarize("runner", output, "")["passed"])

    def test_duplicate_case_cannot_replace_missing_case(self):
        output = junit("runner").replace("touchTargetsFollowLetterboxedGameAfterKeyboardResize",
                                            "handheldKeyboardRejectsSubmitAndHasNoStoredComposingText")
        self.assertFalse(subject.summarize("runner", output, "")["passed"])

    def test_started_but_unfinished_test_is_rejected(self):
        output = junit("tls").rsplit("INSTRUMENTATION_STATUS_CODE: 0", 1)[0]
        self.assertFalse(subject.summarize("tls", output, tls())["passed"])

    def test_framework_crash_is_reported_without_arbitrary_message(self):
        output = ("INSTRUMENTATION_RESULT: shortMsg=Process crashed.\n"
                  "INSTRUMENTATION_RESULT: longMsg=private details\n"
                  "INSTRUMENTATION_STATUS_CODE: -1\nINSTRUMENTATION_CODE: 0\n")
        result = subject.summarize("d3d", output, "")
        self.assertFalse(result["passed"])
        self.assertTrue(result["framework_process_crash_reported"])
        self.assertEqual(result["test_start_status_count"], 0)
        self.assertEqual(result["final_result_codes"], [0])
        self.assertEqual(result["non_success_status_codes"], [-1])
        self.assertNotIn("private details", str(result))

    def test_outer_timeout_rejects_otherwise_complete_output(self):
        self.assertFalse(subject.summarize("tls", junit("tls"), tls(), 124)["passed"])

    def test_crlf_protocol_is_equivalent_to_lf(self):
        self.assertTrue(subject.summarize("runner", junit("runner").replace("\n", "\r\n"), "")["passed"])

    def test_out_of_range_status_code_is_rejected(self):
        output = junit("runner") + "INSTRUMENTATION_STATUS_CODE: -1000\n"
        self.assertFalse(subject.summarize("runner", output, "")["passed"])

    def test_malformed_final_code_is_rejected(self):
        output = junit("runner") + "INSTRUMENTATION_CODE: invalid\n"
        self.assertFalse(subject.summarize("runner", output, "")["passed"])

    def test_status_after_final_completion_is_rejected(self):
        output = "INSTRUMENTATION_CODE: -1\n" + junit("runner").replace("INSTRUMENTATION_CODE: -1\n", "")
        self.assertFalse(subject.summarize("runner", output, "")["passed"])

    def test_full_strict_tls_matrix_and_seven_libraries_pass(self):
        self.assertTrue(subject.summarize("tls", junit("tls"), tls())["passed"])

    def test_tls_timeout_is_not_invalid_ca_evidence(self):
        output = tls().replace("error=12045", "error=12002", 1)
        self.assertFalse(subject.summarize("tls", junit("tls"), output)["passed"])

    def test_tls_wrong_mapped_library_is_rejected(self):
        output = tls().replace(subject.TLS_HASHES["libgnutls.so"], "0" * 64)
        self.assertFalse(subject.summarize("tls", junit("tls"), output)["passed"])

    def test_tls_junit_failure_cannot_be_overridden_by_pass_markers(self):
        output = junit("tls").replace("INSTRUMENTATION_STATUS_CODE: 0", "INSTRUMENTATION_STATUS_CODE: -2")
        self.assertFalse(subject.summarize("tls", output, tls())["passed"])

    def test_two_actual_fl11_devices_and_completed_test_pass(self):
        self.assertTrue(subject.summarize("d3d", junit("d3d"), d3d())["passed"])

    def test_lower_feature_level_is_rejected(self):
        output = d3d().replace("feature=0x0000B000", "feature=0x0000A000")
        self.assertFalse(subject.summarize("d3d", junit("d3d"), output)["passed"])

    def test_duplicate_partial_d3d_snapshot_is_rejected(self):
        self.assertFalse(subject.summarize("d3d", junit("d3d"), d3d() + "\n" + d3d())["passed"])

    def test_empty_game_surface_cannot_pass_as_frames(self):
        output = "UNCLASSIFIED_STARTUP_DIAGNOSTIC_ONLY handheld-UNCLASSIFIED-startup-123.png"
        self.assertFalse(subject.summarize("game", junit("game"), output)["passed"])

    def test_game_capture_remains_pending_visual_and_input_review(self):
        lines = [f"Captured actual game render: handheld-game-123-{index}.png 1280x720" for index in range(3)]
        lines.append("HANDHELD_GAME_FRAME_CAPTURE_COMPLETE_REQUIRES_VISUAL_REVIEW handheld-game-captures-123.json")
        result = subject.summarize("game", junit("game"), "\n".join(lines))
        self.assertTrue(result["passed"])
        self.assertTrue(result["visual_login_review_required"])
        self.assertFalse(result["dummy_input_test_completed"])
        self.assertFalse(result["credential_use_approved"])


if __name__ == "__main__":
    unittest.main()
