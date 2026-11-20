"""Pure boundary regressions: no Docker, Android, network or cloud execution."""
import importlib.util
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'owned_native_runner', Path(__file__).with_name('run_arm64_apk_lab.py'))
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def completed(code=0, output=''):
    return CompletedProcess([], code, output, '')


def environment_command(output):
    calls = []

    def command(args, timeout):
        calls.append(args)
        return completed(output='123\n' if len(calls) == 1 else output)

    return command


def fresh_process_command(stop_exit=0, pid_exit=1, pid_output=''):
    def command(args, timeout):
        if 'force-stop' in args:
            return completed(stop_exit)
        if '/system/bin/pidof' in args:
            return completed(pid_exit, pid_output)
        raise AssertionError('Unexpected command in mocked component boundary')

    return command


class NativeRunnerBoundaryTests(unittest.TestCase):
    valid_environment = (
        'BOOTCLASSPATH=/apex/com.android.art/javalib/core-oj.jar:'
        '/system/framework/framework.jar\nANDROID_ROOT=/system\n')

    def setUp(self):
        parser = SimpleNamespace(summarize_instrumentation=lambda stage, output, exit_code:
                                 {'passed': exit_code == 0 and output == 'COMPLETE'})
        self.parser_patch = patch.object(lab, 'sibling', return_value=parser)
        self.parser_patch.start()
        self.addCleanup(self.parser_patch.stop)

    def test_platform_classpath_accepted(self):
        values = lab.boot_environment(['docker'], 'cid', environment_command(self.valid_environment))
        self.assertEqual(values['ANDROID_ROOT'], '/system')

    def test_classpath_traversal_and_private_paths_rejected(self):
        for value in (
            '/system/../../data/private.jar', '/system/./framework.jar',
            '/apex/com.android.art/../private.jar', '/data/private.jar',
            '/system/framework/framework.jar:/system/../private.jar',
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.boot_environment(['docker'], 'cid', environment_command(
                    'BOOTCLASSPATH=' + value + '\nANDROID_ROOT=/system\n'))

    def test_unknown_or_duplicate_environment_keys_rejected(self):
        for extra in ('OWNER_TOKEN=hidden\n', 'ANDROID_ROOT=/system\n'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                lab.boot_environment(['docker'], 'cid', environment_command(self.valid_environment + extra))

    def test_force_stop_failure_prevents_instrumentation(self):
        with patch.object(lab, 'bounded_capture') as capture:
            with self.assertRaises(ValueError):
                lab.component_tests(['docker'], 'cid', fresh_process_command(stop_exit=1), {}, 10**12)
            capture.assert_not_called()

    def test_inconclusive_pid_check_prevents_instrumentation(self):
        for exit_code in (0, 127):
            with self.subTest(exit_code=exit_code), patch.object(lab, 'bounded_capture') as capture:
                with self.assertRaises(ValueError):
                    lab.component_tests(['docker'], 'cid', fresh_process_command(pid_exit=exit_code), {}, 10**12)
                capture.assert_not_called()

    def test_surviving_process_prevents_instrumentation(self):
        with patch.object(lab, 'bounded_capture') as capture:
            with patch.object(lab.time, 'monotonic', side_effect=[0, 0, 6]):
                with self.assertRaises(ValueError):
                    lab.component_tests(['docker'], 'cid', fresh_process_command(pid_exit=0, pid_output='123'), {}, 10**12)
            capture.assert_not_called()

    def test_failed_or_incomplete_stage_prevents_next_stage(self):
        for result in (('', 0, False, False), ('COMPLETE', 1, False, False),
                       ('COMPLETE', 0, True, False), ('COMPLETE', 0, False, True)):
            with self.subTest(result=result), patch.object(lab, 'bounded_capture', return_value=result) as capture:
                outcome = lab.component_tests(['docker'], 'cid', fresh_process_command(), {}, 10**12)
                self.assertFalse(outcome['passed'])
                self.assertEqual(set(outcome['stages']), {'drawable'})
                self.assertEqual(capture.call_count, 1)

    def test_success_runs_separate_drawable_and_runner_stages(self):
        with patch.object(lab, 'bounded_capture', return_value=('COMPLETE', 0, False, False)) as capture:
            outcome = lab.component_tests(['docker'], 'cid', fresh_process_command(), {}, 10**12)
            self.assertTrue(outcome['passed'])
            self.assertEqual(set(outcome['stages']), {'drawable', 'runner'})
            self.assertEqual(capture.call_count, 2)
            self.assertIn('handheldDrawableAbi', capture.call_args_list[0].args[0])
            self.assertNotIn('handheldDrawableAbi', capture.call_args_list[1].args[0])


def runtime_junit(stage):
    parser = lab.sibling('verify_apk_lab_results')
    lines = []
    for classname, test in sorted(parser.EXPECTED_TESTS[stage]):
        for code in (1, 0):
            lines += ['INSTRUMENTATION_STATUS: class=' + classname,
                      'INSTRUMENTATION_STATUS: test=' + test,
                      'INSTRUMENTATION_STATUS_CODE: ' + str(code)]
    return '\n'.join(lines + ['OK (1 tests)', 'INSTRUMENTATION_CODE: -1'])


def runtime_log(stage):
    if stage == 'd3d':
        return '\n'.join((
            'WOW_D3D11_PROBE_V1',
            'DEFAULT attempted=1 hr=0x00000000 feature=0x0000b000 device=1 context=1',
            'EXPLICIT attempted=1 hr=0x00000000 feature=0x0000b000 device=1 context=1',
            'WOW_D3D11_PROBE_RESULT default_fl11=1 adapter_fl11=1',
            'WOW_D3D11_PROBE_PASS',
            'HANDHELD_D3D11_DEVICE_PROBE_PASS_NOT_GAME_OR_LOADED_PE_ATTESTATION'))
    parser = lab.sibling('verify_apk_lab_results')
    matrix = [f'{case} status={status} error={error} certificate_flags={flags}'
              for case, status, error, flags in parser.TLS_MATRIX]
    mapping = ', '.join(f'{name}={value}' for name, value in sorted(parser.TLS_HASHES.items()))
    return '\n'.join(matrix + [f'Verified TLS mappings in native PID 123: {{{mapping}}}',
                              'WINE_TLS_PASS', 'WINE_TLS_EXTENDED_PASS',
                              'HANDHELD_INSTALLED_WINE_TLS_PASS'])


def runtime_command(log_reset_exit=0, stop_exit=0):
    fresh = fresh_process_command(stop_exit=stop_exit)

    def command(args, timeout):
        if '/system/bin/logcat' in args:
            assert args[-3:] == ['-b', 'main', '-c']
            return completed(log_reset_exit)
        return fresh(args, timeout)

    return command


class InstalledRuntimeStageTests(unittest.TestCase):
    def run_stage(self, stage, captures, **command_options):
        with patch.object(lab, 'bounded_capture', side_effect=captures) as capture:
            result = lab.runtime_test(stage, ['docker'], 'cid', runtime_command(**command_options),
                                      {'ANDROID_ROOT': '/system'}, 10**12)
        return result, capture

    def test_exact_fixtures_and_specialized_contracts_pass_separately(self):
        for stage, flag, tag in (('d3d', 'handheldD3dProbe', 'HandheldD3dProbe'),
                                 ('tls', 'handheldWineTls', 'HandheldWineTlsTest')):
            with self.subTest(stage=stage):
                result, capture = self.run_stage(stage, [(runtime_junit(stage), 0, False, False),
                                                        (runtime_log(stage), 0, False, False)])
                self.assertTrue(result['passed'])
                self.assertFalse(result['credential_use_approved'])
                self.assertIn(flag, capture.call_args_list[0].args[0])
                self.assertNotIn('handheldDrawableAbi', capture.call_args_list[0].args[0])
                self.assertEqual(capture.call_args_list[1].args[0][-3:], ['-s', tag + ':I', '*:S'])
                self.assertTrue(result['milestones']['fresh_process'])
                self.assertTrue(result['milestones']['main_log_reset'])
                self.assertIn('instrumentation', result['timings_seconds'])
                self.assertIn('selected_tag_capture', result['timings_seconds'])

    def test_junit_without_numeric_runtime_evidence_rejected(self):
        for stage in ('d3d', 'tls'):
            with self.subTest(stage=stage):
                result, _ = self.run_stage(stage, [(runtime_junit(stage), 0, False, False),
                                                  ('', 0, False, False)])
                self.assertFalse(result['passed'])
                self.assertTrue(result['verification']['junit_passed'])

    def test_numeric_runtime_evidence_without_junit_rejected(self):
        for stage in ('d3d', 'tls'):
            with self.subTest(stage=stage):
                result, _ = self.run_stage(stage, [('', 0, False, False),
                                                  (runtime_log(stage), 0, False, False)])
                self.assertFalse(result['passed'])

    def test_nonzero_timeout_or_output_cap_cannot_pass_runtime(self):
        for exit_code, timeout, limit in ((7, False, False), (0, True, False), (0, False, True)):
            with self.subTest(exit_code=exit_code, timeout=timeout, limit=limit):
                result, _ = self.run_stage('d3d', [(runtime_junit('d3d'), exit_code, timeout, limit),
                                                  (runtime_log('d3d'), 0, False, False)])
                self.assertFalse(result['passed'])
                self.assertEqual(result['instrumentation_exit_code'], exit_code)
                self.assertEqual(result['verification']['transport_exited_zero'], exit_code == 0)

    def test_selected_log_nonzero_timeout_or_cap_cannot_pass(self):
        for exit_code, timeout, limit in ((1, False, False), (0, True, False), (0, False, True)):
            with self.subTest(exit_code=exit_code, timeout=timeout, limit=limit):
                result, _ = self.run_stage('tls', [(runtime_junit('tls'), 0, False, False),
                                                  (runtime_log('tls'), exit_code, timeout, limit)])
                self.assertTrue(result['verification']['passed'])
                self.assertFalse(result['passed'])
                self.assertFalse(result['milestones']['selected_tag_captured'])

    def test_log_reset_failure_prevents_any_probe(self):
        result, capture = self.run_stage('d3d', [], log_reset_exit=1)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failure_phase'], 'log_reset')
        self.assertFalse(result['milestones']['instrumentation_started'])
        capture.assert_not_called()

    def test_force_stop_failure_prevents_any_probe(self):
        result, capture = self.run_stage('tls', [], stop_exit=1)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failure_phase'], 'fresh_process')
        capture.assert_not_called()

    def test_numeric_diagnostics_bound_rows_and_never_export_unknown_text(self):
        private = 'private-canary@example.invalid C:\\private-unknown-file'
        result = lab.runtime_diagnostics('d3d', '\n'.join(
            ['LOAD_DXGI ok=1 error=0'] * 40 + [private, 'LOAD_UNKNOWN ok=1 error=0']))
        self.assertEqual(len(result['numeric_records']), 32)
        self.assertTrue(result['numeric_records_truncated'])
        self.assertNotIn(private, json.dumps(result))
        self.assertNotIn('LOAD_UNKNOWN', json.dumps(result))

    def test_unimplemented_game_and_all_reject_before_input_or_lab(self):
        for stage in ('game', 'all'):
            with self.subTest(stage=stage), patch('sys.argv', ['lab', '--main', 'absent', '--qa', 'absent',
                                                            '--hal', 'absent', '--stage', stage]), \
                    patch.object(lab, 'verify_input') as verify, patch.object(lab, 'run_lab') as run, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(lab.main(), 2)
                verify.assert_not_called()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
