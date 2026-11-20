"""Pure boundary regressions: no Docker, Android, network or cloud execution."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
