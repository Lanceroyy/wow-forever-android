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
    if stage == 'game':
        return '\n'.join([f'Captured actual game render: handheld-game-123-{index}.png 1280x720'
                          for index in range(3)] + [
            'HANDHELD_GAME_FRAME_CAPTURE_COMPLETE_REQUIRES_VISUAL_REVIEW handheld-game-captures-123.json'])
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
    def run_stage(self, stage, captures, expected_system_server=None, **command_options):
        with patch.object(lab, 'bounded_capture', side_effect=[*captures, ('', 0, False, False)]) as capture:
            result = lab.runtime_test(stage, ['docker'], 'cid', runtime_command(**command_options),
                                      {'ANDROID_ROOT': '/system'}, 10**12, expected_system_server)
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
                self.assertTrue(result['system_log_after_failed_fixture']['read_complete'])

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

    def test_snapshot_experiment_rejects_restarted_system_server_before_test(self):
        before = {'status': 'observed', 'pid': 123, 'start_ticks': 333}
        after = {'status': 'observed', 'pid': 123, 'start_ticks': 444}
        with patch.object(lab, 'platform_process_snapshot', return_value={'system_server': after}):
            result, capture = self.run_stage('d3d', [], expected_system_server=before)
        self.assertFalse(result['passed'])
        self.assertFalse(result['snapshot_control_server_matches'])
        self.assertFalse(result['milestones']['instrumentation_started'])
        capture.assert_not_called()

    def test_numeric_diagnostics_bound_rows_and_never_export_unknown_text(self):
        private = 'private-canary@example.invalid C:\\private-unknown-file'
        result = lab.runtime_diagnostics('d3d', '\n'.join(
            ['LOAD_DXGI ok=1 error=0'] * 40 + [private, 'LOAD_UNKNOWN ok=1 error=0']))
        self.assertEqual(len(result['numeric_records']), 32)
        self.assertTrue(result['numeric_records_truncated'])
        self.assertNotIn(private, json.dumps(result))
        self.assertNotIn('LOAD_UNKNOWN', json.dumps(result))

    def test_unimplemented_all_rejects_before_input_or_lab(self):
        for stage in ('all',):
            with self.subTest(stage=stage), patch('sys.argv', ['lab', '--main', 'absent', '--qa', 'absent',
                                                            '--hal', 'absent', '--stage', stage]), \
                    patch.object(lab, 'verify_input') as verify, patch.object(lab, 'run_lab') as run, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(lab.main(), 2)
                verify.assert_not_called()
                run.assert_not_called()


    def test_baseline_game_capture_preserves_pending_visual_and_input_gates(self):
        result, capture = self.run_stage('game', [(runtime_junit('game'), 0, False, False),
                                                (runtime_log('game'), 0, False, False)])
        self.assertTrue(result['passed'])
        self.assertTrue(result['automated_game_capture_passed'])
        self.assertEqual(result['verdict_scope'], 'automated_game_capture_only')
        self.assertTrue(result['visual_login_review_required'])
        self.assertFalse(result['visible_login_confirmed'])
        self.assertFalse(result['dummy_input_test_completed'])
        self.assertFalse(result['credential_use_approved'])
        self.assertFalse(result['account_input_sent'])
        args = capture.call_args_list[0].args[0]
        self.assertIn('app.gamenative.security.HandheldGameFrameTest', args)
        self.assertIn('handheldGameFrame', args)
        for forbidden in ('handheldDirectGame', 'handheldStartupErrors', 'handheldStartupDiagnostic',
                          'handheldFrameWarmupSeconds'):
            self.assertNotIn(forbidden, args)
        self.assertEqual(capture.call_args_list[0].args[1], 2100)
        self.assertEqual(result['verification']['game_frames_captured'], 3)

    def test_unclassified_game_diagnostic_cannot_pass_as_game_frames(self):
        result, _ = self.run_stage('game', [(runtime_junit('game'), 0, False, False),
                                           ('UNCLASSIFIED_STARTUP_DIAGNOSTIC_ONLY '
                                            'handheld-UNCLASSIFIED-startup-123.png', 0, False, False)])
        self.assertFalse(result['passed'])
        self.assertFalse(result['automated_game_capture_passed'])
        self.assertEqual(result['verification']['game_frames_captured'], 0)

    def test_failed_game_log_capture_cannot_pass_even_with_all_frames(self):
        for exit_code, timeout, limit in ((1, False, False), (0, True, False), (0, False, True)):
            with self.subTest(exit_code=exit_code, timeout=timeout, limit=limit):
                result, _ = self.run_stage('game', [(runtime_junit('game'), 0, False, False),
                                                   (runtime_log('game'), exit_code, timeout, limit)])
                self.assertFalse(result['passed'])
                self.assertFalse(result['automated_game_capture_passed'])
                self.assertTrue(result['visual_login_review_required'])

    def test_failed_game_preflight_still_cannot_approve_visual_or_input(self):
        result, capture = self.run_stage('game', [], stop_exit=1)
        self.assertFalse(result['passed'])
        self.assertTrue(result['visual_login_review_required'])
        self.assertFalse(result['visible_login_confirmed'])
        self.assertFalse(result['dummy_input_test_completed'])
        self.assertFalse(result['credential_use_approved'])
        capture.assert_not_called()

    def test_game_diagnostics_export_only_fixed_counts_and_errors(self):
        private = 'unknown-private-class-canary@example.invalid'
        log = '\n'.join((
            'Preparation: Downloading first-run runtime',
            'Session: Preparing Wine and graphics',
            'Startup gate: game_windows=0 game_processes=1 mapped_classes=' + private,
            'Startup gate: game_windows=1 game_processes=1 mapped_classes=' + private,
            'Actual game window/process observed; waiting 120s before capture',
            'Captured actual game render: handheld-game-123-0.png 1280x720',
            'UNCLASSIFIED_STARTUP_DIAGNOSTIC_ONLY /private/' + private + '.png',
        ))
        result = lab.game_diagnostics(log, 'stack=java.lang.AssertionError: Wine exited before a game frame: 0')
        self.assertEqual(result['last_startup_gate'], {'game_windows': 1, 'game_processes': 1})
        self.assertTrue(result['strict_game_window_and_process_observed'])
        self.assertEqual(result['frame_observations'], [{'index': 0, 'width': 1280, 'height': 720}])
        self.assertEqual(result['fixed_wine_exit_codes']['wine_exit_before_frame'], [0])
        self.assertTrue(result['preparation_phases_observed']['rootfs_download'])
        self.assertTrue(result['baseline_production_wrapper_selected'])
        self.assertFalse(result['direct_game_diagnostic_selected'])
        self.assertNotIn(private, json.dumps(result))
        self.assertNotIn('handheld-game-123-0.png', json.dumps(result))

    def test_game_diagnostics_bound_samples_and_keep_latest_counts(self):
        log = '\n'.join(f'Startup gate: game_windows=0 game_processes={index} mapped_classes=[]'
                        for index in range(40))
        result = lab.game_diagnostics(log, 'Wine exited before capture: -2147483648')
        self.assertEqual(len(result['startup_gate_samples']), 32)
        self.assertTrue(result['startup_gate_samples_truncated'])
        self.assertEqual(result['last_startup_gate']['game_processes'], 39)
        self.assertEqual(result['fixed_wine_exit_codes']['wine_exit_before_capture'], [-2147483648])


class StockProfileBoundaryTests(unittest.TestCase):
    def test_stock_install_requires_original_properties_without_hal_claim(self):
        calls = []

        def command(args, seconds):
            calls.append(args)
            if '/system/bin/sha256sum' in args:
                path = args[-1]
                digest = {
                    '/linkerconfig/ld.config.txt': 'bc09359d857bcd4a9028ee0bb2e0bfd99f8bb74de2c1c4ffe67fa00aff2620b1',
                    '/system/build.prop': '38a385a1ae931aabc0e4950a506c1f3eeeb84f141ad0a2620b2223183845082e',
                    '/data/app/main/base.apk': lab.MAIN_SHA, '/data/app/qa/base.apk': lab.QA_SHA,
                }[path]
                return completed(output=digest + '  public-file\n')
            if '/system/bin/getprop' in args:
                self.assertEqual(args[-1], 'ro.hardware.vulkan')
                return completed(output='pastel\n')
            if 'cp' in args:
                return completed()
            if '/system/bin/pm' in args:
                if 'install' in args:
                    return completed(output='Success\n')
                return completed(output='package:/data/app/' + ('main' if args[-1] == lab.PACKAGE else 'qa') + '/base.apk\n')
            raise AssertionError('Unexpected stock prerequisite')

        progress = {}
        with patch.object(lab, 'component_tests', return_value={'passed': True}):
            result = lab.installed_component_stage(['docker'], 'cid', command, {}, None,
                SimpleNamespace(main=Path('main.apk'), qa=Path('qa.apk'), stage='native'), 10**12, progress)
        self.assertTrue(result['passed'])
        self.assertEqual(result['graphics_profile'], 'stock')
        self.assertFalse(result['nine_public_hal_files_hash_checked_and_mapped'])
        self.assertEqual(progress['phase'], 'complete')
        self.assertEqual(progress['hal_files_verified'], 0)
        self.assertTrue(all(value['installed_base_digest_verified'] for value in progress['packages'].values()))
        self.assertFalse(any('/system/bin/pidof' in args or '/system/bin/cat' in args for args in calls))

    def test_namespace_failure_records_phase_without_copy_or_raw_output(self):
        progress = {}
        private = 'private-system-error-canary'
        with self.assertRaises(ValueError):
            lab.installed_component_stage(['docker'], 'cid', lambda args, seconds: completed(output=private),
                {}, None, SimpleNamespace(stage='native'), 10**12, progress)
        self.assertEqual(progress['phase'], 'namespace')
        self.assertNotIn(private, json.dumps(progress))
        self.assertEqual(progress['packages'], {})

    def test_data_mounter_guard_rejects_same_parent_or_child_data_bind(self):
        data = Path('owned-lab-data-placeholder').resolve()
        for path in (data, data.parent, data / 'descendant'):
            with self.subTest(path=str(path)):
                def command(args, seconds):
                    if 'ps' in args:
                        return completed(output='a' * 64)
                    return completed(output=json.dumps([{'State': {'Running': True},
                        'Mounts': [{'Type': 'bind', 'Source': str(path)}]}]))
                with self.assertRaises(AssertionError):
                    lab.assert_no_data_mounters(['docker'], data, command)

    def test_no_running_data_mounter_is_allowed(self):
        lab.assert_no_data_mounters(['docker'], Path('owned-data').resolve(),
                                   lambda args, seconds: completed())

    def test_platform_snapshot_exports_numeric_identity_not_raw_stat(self):
        def command(args, seconds):
            if '/system/bin/pidof' in args:
                return completed(output='123\n' if args[-1] == 'surfaceflinger' else '456\n')
            name, pid = ('surfaceflinger', '123') if '/proc/123/stat' in args else ('system_server', '456')
            return completed(output=pid + ' (' + name + ') S ' + ' '.join(['0'] * 18 + ['98765', '0']))
        result = lab.platform_process_snapshot(['docker'], 'cid', command)
        self.assertEqual(result['surfaceflinger'], {'status': 'observed', 'pid': 123, 'start_ticks': 98765})
        self.assertEqual(result['system_server'], {'status': 'observed', 'pid': 456, 'start_ticks': 98765})

    def test_optional_process_snapshot_timeout_is_unavailable(self):
        def command(args, seconds):
            raise TimeoutError('private-runtime-canary')
        result = lab.platform_process_snapshot(['docker'], 'cid', command)
        self.assertTrue(all(value == {'status': 'unavailable'} for value in result.values()))
        self.assertNotIn('private-runtime-canary', json.dumps(result))


class SnapshotControlBoundaryTests(unittest.TestCase):
    marker = 'LAB_SNAPSHOT_API_SET enabled=false proxy=android.view.IWindowManager$Stub$Proxy'

    @staticmethod
    def state(enabled, secure='false', cache='Task'):
        return ('WINDOW MANAGER\nmDisableSecureWindows=' + secure + '\n'
                'mSnapshotEnabled=' + enabled + '\n  SnapshotCache ' + cache + '\n')

    def attempt(self, captures=None, identities=None, remote_hash=None):
        original = {'status': 'observed', 'pid': 123, 'start_ticks': 456}
        process_states = [{'system_server': value} for value in (identities or [original] * 3)]
        captured = captures if captures is not None else [
            (self.state('true'), 0, False, False), (self.marker, 0, False, False),
            (self.state('false'), 0, False, False)]
        progress = {}

        def command(args, seconds):
            if 'cp' in args:
                return completed()
            if '/system/bin/sha256sum' in args:
                return completed(output=(remote_hash or lab.SNAPSHOT_DEX_SHA) + '  lab.dex')
            if '/system/bin/stat' in args:
                return completed(output='2444')
            raise AssertionError('Unexpected snapshot-control command')

        with patch.object(lab, 'verify_input') as verify, \
                patch.object(lab, 'platform_process_snapshot', side_effect=process_states), \
                patch.object(lab, 'bounded_capture', side_effect=captured) as capture:
            failure = None
            try:
                lab.disable_lab_task_snapshots(['docker'], 'cid', command,
                    {'BOOTCLASSPATH': '/system/framework/framework.jar', 'ANDROID_ROOT': '/system'},
                    Path('public-lab.dex'), progress, 10**12)
            except Exception as error:
                failure = error
        return progress, failure, capture, verify

    def test_real_proxy_confirmation_and_same_server_postconditions_required(self):
        result, failure, capture, verify = self.attempt()
        self.assertIsNone(failure)
        self.assertTrue(result['applied_and_verified'])
        self.assertTrue(result['task_snapshot_enabled_before'])
        self.assertFalse(result['task_snapshot_enabled_after'])
        self.assertFalse(result['secure_window_override_before'])
        self.assertFalse(result['secure_window_override_after'])
        self.assertTrue(result['same_system_server_pid_and_start_ticks'])
        self.assertEqual(result['phase'], 'complete')
        verify.assert_called_once_with(Path('public-lab.dex'), 2444, lab.SNAPSHOT_DEX_SHA)
        args = capture.call_args_list[1].args[0]
        self.assertIn('BOOTCLASSPATH=/system/framework/framework.jar', args)
        self.assertIn('CLASSPATH=/data/local/tmp/handheld-task-snapshot-control-ec3f.dex', args)
        self.assertEqual(args[-4:], ['/system/bin/app_process', '/system/bin', 'LabSnapshotControl', 'disable'])

    def test_restart_or_pid_reuse_rejects_postcondition(self):
        original = {'status': 'observed', 'pid': 123, 'start_ticks': 456}
        for changed in ({'status': 'observed', 'pid': 124, 'start_ticks': 457},
                        {'status': 'observed', 'pid': 123, 'start_ticks': 999}):
            with self.subTest(changed=changed):
                result, failure, _, _ = self.attempt(identities=[original, original, changed])
                self.assertIsInstance(failure, ValueError)
                self.assertFalse(result['applied_and_verified'])
                self.assertFalse(result['same_system_server_pid_and_start_ticks'])

    def test_secure_override_true_or_mixed_rejected_before_setter(self):
        for state in (self.state('true', 'true'), self.state('true') + 'mDisableSecureWindows=true\n'):
            with self.subTest(state=state):
                result, failure, capture, _ = self.attempt(captures=[(state, 0, False, False)])
                self.assertIsInstance(failure, ValueError)
                self.assertFalse(result['api_invoked'])
                self.assertFalse(result['applied_and_verified'])
                self.assertEqual(capture.call_count, 1)

    def test_activity_snapshot_flag_cannot_replace_task_flag(self):
        for text in (self.state('false', cache='Activity'),
                     self.state('true') + self.state('false')):
            with self.subTest(text=text):
                result, failure, _, _ = self.attempt(captures=[(text, 0, False, False)])
                self.assertIsInstance(failure, ValueError)
                self.assertFalse(result['api_invoked'])

    def test_still_enabled_after_setter_rejected(self):
        result, failure, _, _ = self.attempt(captures=[
            (self.state('true'), 0, False, False), (self.marker, 0, False, False),
            (self.state('true'), 0, False, False)])
        self.assertIsInstance(failure, ValueError)
        self.assertFalse(result['applied_and_verified'])
        self.assertTrue(result['task_snapshot_enabled_after'])

    def test_missing_marker_or_capture_limits_cannot_apply_control(self):
        for value in (('private-helper-error-canary', 0, False, False),
                      (self.marker, 1, False, False), (self.marker, 0, True, False),
                      (self.marker, 0, False, True)):
            with self.subTest(value=value):
                result, failure, _, _ = self.attempt(captures=[(self.state('true'), 0, False, False), value])
                self.assertIsInstance(failure, ValueError)
                self.assertFalse(result['applied_and_verified'])
                self.assertNotIn('private-helper-error-canary', json.dumps(result))

    def test_wrong_remote_dex_rejected_before_api(self):
        result, failure, capture, _ = self.attempt(remote_hash='0' * 64)
        self.assertIsInstance(failure, ValueError)
        self.assertFalse(result['api_invoked'])
        self.assertFalse(result['applied_and_verified'])
        self.assertEqual(capture.call_count, 1)

    def test_option_restricted_to_graphics_lab_stages(self):
        for stage in ('native', 'tls'):
            with self.subTest(stage=stage), patch('sys.argv', ['lab', '--main', 'absent', '--qa', 'absent',
                    '--hal', 'absent', '--stage', stage, '--snapshot-control-dex', 'absent']), \
                    patch.object(lab, 'verify_input') as verify, patch.object(lab, 'run_lab') as run, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(lab.main(), 2)
                verify.assert_not_called()
                run.assert_not_called()

    def test_default_option_does_not_request_control(self):
        with patch('sys.argv', ['lab', '--main', 'main', '--qa', 'qa', '--hal', 'hal', '--stage', 'd3d']), \
                patch.object(lab, 'verify_input'), patch.object(lab, 'run_lab', return_value=0) as run:
            self.assertEqual(lab.main(), 0)
            self.assertIsNone(run.call_args.args[0].snapshot_control_dex)


class StockComparatorOptionTests(unittest.TestCase):
    def test_stock_comparator_keeps_exact_selected_graphics_stage(self):
        for stage in ('d3d', 'game'):
            with self.subTest(stage=stage), patch('sys.argv', ['lab', '--main', 'main', '--qa', 'qa',
                    '--hal', 'hal', '--stage', stage, '--stock-graphics']), \
                    patch.object(lab, 'verify_input'), patch.object(lab, 'run_lab', return_value=0) as run:
                self.assertEqual(lab.main(), 0)
                self.assertTrue(run.call_args.args[0].stock_graphics)
                self.assertEqual(run.call_args.args[0].stage, stage)
                self.assertIsNone(run.call_args.args[0].snapshot_control_dex)

    def test_stock_comparator_rejects_other_modes_and_snapshot_mutation(self):
        for options in (['--stage', 'native'], ['--stage', 'tls'],
                        ['--stage', 'd3d', '--snapshot-control-dex', 'lab.dex']):
            with self.subTest(options=options), patch('sys.argv', ['lab', '--main', 'absent', '--qa', 'absent',
                    '--hal', 'absent', '--stock-graphics'] + options), \
                    patch.object(lab, 'verify_input') as verify, patch.object(lab, 'run_lab') as run, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(lab.main(), 2)
                verify.assert_not_called()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
