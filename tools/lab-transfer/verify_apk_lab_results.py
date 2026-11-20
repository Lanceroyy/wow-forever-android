#!/usr/bin/env python3
"""Read bounded local lab output; emit fixed, credential-free result fields only.

An instrumentation process exiting zero is insufficient. Each expected test must
finish successfully, without skips, and AndroidJUnitRunner must report completion.
Game capture success remains subject to separate image review.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


PACKAGE = "app.gamenative.security."
RUNNER_TESTS = {
    (PACKAGE + "HandheldRunnerTest", name) for name in (
        "handheldIsPrivateAndHasOnlyItsLauncherExported",
        "inputOnlyBridgeWritesControllerStateAndDisconnectsWithoutCommandSocket",
        "sessionFocusReleaseKeepsConnectionUntilActualDisconnect",
        "handheldKeyboardRejectsSubmitAndHasNoStoredComposingText",
        "winePreparationFailureIsReportedInsteadOfLeavingEmptySurface",
        "touchTargetsFollowLetterboxedGameAfterKeyboardResize",
        "protectedHandheldWindowRendersGeneratedX11Colors",
        "destroyingQueuedLaunchDoesNotLaunchTheReopenedActivity",
    )
}
EXPECTED_TESTS = {
    "runner": RUNNER_TESTS,
    "drawable": {(PACKAGE + "HandheldDrawableAbiTest", "nativeDrawingAndCopiesPreserveRawBgra")},
    "d3d": {(PACKAGE + "HandheldD3dProbeTest", "installedWineCreatesFeatureLevel11Device")},
    "tls": {(PACKAGE + "HandheldWineTlsTest", "installedWineRejectsInvalidCertificates")},
    "game": {(PACKAGE + "HandheldGameFrameTest", "capturesFramesFromActualWineGame")},
}
TLS_HASHES = {
    "libgmp.so": "69e191b29adc33abc6412f50c7dee3efa23cc44b4d51c4619b5b84369185b212",
    "libgnutls.so": "e15ea56b3def3afa149000a4a9360c8950b3cbae936a9d442fce80424dff3b7b",
    "libhogweed.so.6": "c1c9f83f3730b592e9049e1b2d6edfd4ecf4bd3293c34bd86a69be17c5d60635",
    "libiconv.so": "9f720f58b143730a589f9551ce098262796f8c1d5344f0f894700dfe122f4377",
    "libidn2.so": "49e377c5a45141d2d59aa589616221ee4596730ad3940ed4db2b62d49e3cad0e",
    "libnettle.so.8": "d91a57c30499e8a7b791a234fcb2ed89935fd4e95c13f5b287bd39b4390c87e4",
    "libunistring.so": "3b745c5705160e26b23e8a662d02a55d598f785c3b4ab1ce6272c1c9272f03ee",
}
TLS_MATRIX = [
    ("TRUSTED", 200, 0, 0), ("UNTRUSTED", 0, 12045, 0),
    ("TRUSTED", 200, 0, 0), ("UNTRUSTED", 0, 12045, 0),
    ("EXPIRED", 0, 12037, 0), ("HOSTNAME", 0, 12038, 0),
    ("SELF_SIGNED", 0, 12045, 0),
]
MAX_OUTPUT = 2 * 1024 * 1024


def instrumentation_result(text, kind):
    """Fail closed on partial, skipped, duplicated, or differently selected tests."""
    expected = EXPECTED_TESTS[kind]
    record = {}
    pending = Counter()
    completed = Counter()
    final_codes = []
    errors = 0
    statuses = []
    lines = text.splitlines()
    for line in lines:
        value = re.fullmatch(r"INSTRUMENTATION_STATUS: (class|test)=(.{1,200})", line)
        if value:
            key, content = value.groups()
            if key in record or final_codes:
                errors += 1
            record[key] = content
            continue
        value = re.fullmatch(r"INSTRUMENTATION_STATUS_CODE: (-?[0-9]{1,3})", line)
        if value:
            code = int(value[1])
            statuses.append(code)
            identity = (record.get("class"), record.get("test"))
            if final_codes or identity not in expected or code not in (0, 1):
                errors += 1
            elif code == 1:
                if pending[identity] or completed[identity]:
                    errors += 1
                pending[identity] += 1
            elif pending[identity] != 1 or completed[identity]:
                errors += 1
            else:
                pending[identity] -= 1
                completed[identity] += 1
            record = {}
            continue
        value = re.fullmatch(r"INSTRUMENTATION_CODE: (-?[0-9]{1,3})", line)
        if value:
            final_codes.append(int(value[1]))
        elif line.startswith(("INSTRUMENTATION_CODE", "INSTRUMENTATION_STATUS_CODE",
                              "INSTRUMENTATION_STATUS: class=", "INSTRUMENTATION_STATUS: test=")):
            errors += 1
        if line.startswith(("INSTRUMENTATION_FAILED:", "INSTRUMENTATION_ABORTED:")):
            errors += 1
    ok = [match[1] for line in lines if (match := re.fullmatch(r"OK \(([0-9]{1,3}) tests?\)", line))]
    passed = (
        not errors and not record and final_codes == [-1]
        and ok == [str(len(expected))]
        and set(completed) == expected
        and all(value == 1 for value in completed.values())
        and not any(pending.values())
        and len(statuses) == 2 * len(expected)
    )
    return {
        "junit_passed": passed,
        "expected_tests": len(expected),
        "successful_tests": sum(completed.values()),
        "unexpected_status_or_protocol_errors": errors,
        "final_completion_present": final_codes == [-1],
        "skipped_or_failed_statuses": sum(code not in (0, 1) for code in statuses),
    }


def summarize_instrumentation(stage, output, exit_code):
    """JUnit completion only; specialized stages also require selected-tag checks."""
    result = {"stage": stage, **instrumentation_result(output, stage)}
    result["transport_exited_zero"] = exit_code == 0
    result["passed"] = result["junit_passed"] and result["transport_exited_zero"]
    return result


def tls_result(text):
    lines = text.splitlines()
    matrix = []
    mapping_observations = []
    for line in lines:
        value = re.fullmatch(
            r"(TRUSTED|UNTRUSTED|EXPIRED|HOSTNAME|SELF_SIGNED) "
            r"status=([0-9]{1,10}) error=([0-9]{1,10}) certificate_flags=([0-9]{1,10})", line)
        if value:
            matrix.append((value[1], *(int(item) for item in value.groups()[1:])))
        value = re.fullmatch(r"Verified TLS mappings in native PID [1-9][0-9]{0,9}: \{(.{1,2048})\}", line)
        if value:
            entries = value[1].split(", ")
            parsed = [re.fullmatch(r"(lib[a-zA-Z0-9_.+-]{1,40})=([0-9a-f]{64})", item) for item in entries]
            mapping_observations.append(
                len(entries) == 7 and all(parsed)
                and {item[1]: item[2] for item in parsed} == TLS_HASHES)
    markers = {name: lines.count(name) for name in (
        "WINE_TLS_PASS", "WINE_TLS_FAIL", "WINE_TLS_EXTENDED_PASS", "WINE_TLS_EXTENDED_FAIL",
        "HANDHELD_INSTALLED_WINE_TLS_PASS")}
    passed = (
        matrix == TLS_MATRIX and mapping_observations == [True]
        and markers == {"WINE_TLS_PASS": 1, "WINE_TLS_FAIL": 0,
                        "WINE_TLS_EXTENDED_PASS": 1, "WINE_TLS_EXTENDED_FAIL": 0,
                        "HANDHELD_INSTALLED_WINE_TLS_PASS": 1})
    return {
        "tls_matrix_passed": passed,
        "numeric_case_count": len(matrix),
        "strict_expected_errors_observed": matrix == TLS_MATRIX,
        "approved_seven_mapped_libraries_observed": mapping_observations == [True],
        "markers": markers,
    }


def d3d_result(text):
    lines = text.splitlines()
    device = re.compile(r"(DEFAULT|EXPLICIT) attempted=([01]) hr=0x([0-9a-fA-F]{8}) "
                        r"feature=0x([0-9a-fA-F]{8}) device=([01]) context=([01])")
    results = {"DEFAULT": [], "EXPLICIT": []}
    for line in lines:
        value = device.fullmatch(line)
        if value:
            results[value[1]].append(value[2] == "1" and int(value[3], 16) == 0
                                     and int(value[4], 16) == 0xB000
                                     and value[5] == "1" and value[6] == "1")
    required = (
        "WOW_D3D11_PROBE_V1", "WOW_D3D11_PROBE_RESULT default_fl11=1 adapter_fl11=1",
        "WOW_D3D11_PROBE_PASS", "HANDHELD_D3D11_DEVICE_PROBE_PASS_NOT_GAME_OR_LOADED_PE_ATTESTATION",
    )
    passed = (all(lines.count(marker) == 1 for marker in required)
              and lines.count("WOW_D3D11_PROBE_FAIL") == 0
              and results == {"DEFAULT": [True], "EXPLICIT": [True]})
    return {"d3d_fl11_device_creation_passed": passed,
            "default_hardware_fl11": results["DEFAULT"] == [True],
            "actual_adapter_fl11": results["EXPLICIT"] == [True],
            "game_presentation_review_required": True}


def game_result(text):
    text = "\n".join(text.splitlines())
    capture = re.findall(r"^Captured actual game render: handheld-game-[0-9]{1,16}-([012])\.png "
                         r"([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$", text, re.MULTILINE)
    complete = re.findall(
        r"^HANDHELD_GAME_FRAME_CAPTURE_COMPLETE_REQUIRES_VISUAL_REVIEW "
        r"handheld-game-captures-[0-9]{1,16}\.json$", text, re.MULTILINE)
    return {"actual_game_capture_sequence_complete":
            len(complete) == 1 and [value[0] for value in capture] == ["0", "1", "2"],
            "game_frames_captured": len(capture),
            "visual_login_review_required": True,
            "dummy_input_test_completed": False,
            "credential_use_approved": False}


def summarize(kind, instrumentation, log, exit_code=0):
    result = {"stage": kind, **instrumentation_result(instrumentation, kind)}
    checks = {}
    if kind == "tls":
        checks = tls_result(log)
        stage_passed = result["junit_passed"] and checks["tls_matrix_passed"]
    elif kind == "d3d":
        checks = d3d_result(log)
        stage_passed = result["junit_passed"] and checks["d3d_fl11_device_creation_passed"]
    elif kind == "game":
        checks = game_result(log)
        stage_passed = result["junit_passed"] and checks["actual_game_capture_sequence_complete"]
    else:
        stage_passed = result["junit_passed"]
    result.update(checks)
    result["transport_exited_zero"] = exit_code == 0
    result["automated_stage_checks_passed"] = stage_passed and exit_code == 0
    result["passed"] = result["automated_stage_checks_passed"]
    result["instrumentation_sha256"] = hashlib.sha256(instrumentation.encode()).hexdigest()
    result["selected_log_sha256"] = hashlib.sha256(log.encode()).hexdigest()
    return result


def bounded_text(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_OUTPUT + 1)
    if len(data) > MAX_OUTPUT:
        raise ValueError("Bounded output exceeded")
    return data.decode("utf-8", errors="strict")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=tuple(EXPECTED_TESTS))
    parser.add_argument("instrumentation_file")
    parser.add_argument("--selected-log-file")
    parser.add_argument("--transport-exit-code", type=int, required=True,
                        help="Actual docker/ADB return code; do not infer it from saved output")
    args = parser.parse_args()
    try:
        result = summarize(args.stage, bounded_text(args.instrumentation_file),
                           bounded_text(args.selected_log_file) if args.selected_log_file else "",
                           args.transport_exit_code)
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({"stage": args.stage, "automated_stage_checks_passed": False,
                          "bounded_result_read_failed": True}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["automated_stage_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
