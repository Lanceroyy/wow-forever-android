#!/usr/bin/env python3
"""Owned native ARM64 Android component lab. Preparation only until reviewed.

Consumes three already downloaded local pinned inputs. It never downloads private
artifacts or receives an owner token. Select one isolated native9, D3D11 device,
Wine certificate, or baseline game-capture stage. Actual login and input still
require separate review; no stage enters account text or publishes app files.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

MAIN_SHA = '1b8e7a3be0dc490b727ec22a7d548dd1c65423ca57e7e6abe18695b10202f62e'
QA_SHA = '859b6cc24945988fb8a55d32fc5152fee8b52dddc91d52f987b261f1a6003ac0'
HAL_SHA = 'dc9352ca778f7fce4510fd51f1dcadd9eed6573fb3bb4fd9fd3506fdafbb943a'
PACKAGE = 'app.wowforever.handheld'
RUNNER = PACKAGE + '.test/androidx.test.runner.AndroidJUnitRunner'
BOOT_KEYS = ('BOOTCLASSPATH', 'DEX2OATBOOTCLASSPATH', 'SYSTEMSERVERCLASSPATH',
             'ANDROID_ROOT', 'ANDROID_RUNTIME_ROOT', 'ANDROID_DATA', 'ANDROID_ART_ROOT',
             'ANDROID_I18N_ROOT', 'ANDROID_TZDATA_ROOT')


def sibling(name):
    path = Path(__file__).resolve().with_name(name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_input(path, size, expected):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
        raise ValueError('Pinned input size/type differs')
    with path.open('rb') as source:
        result = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    if result.hexdigest() != expected:
        raise ValueError('Pinned input checksum differs')


def bounded_capture(argv, timeout):
    """Private unlinked output with a kernel-enforced 2MiB bound; never export raw."""
    import resource
    limit = 2 * 1024 * 1024
    def bound_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT,
                                   preexec_fn=bound_output, start_new_session=True)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=10)
        output.seek(0)
        raw = output.read(limit + 1)
    return raw.decode('utf-8', errors='replace'), process.returncode, timed_out, len(raw) >= limit


def boot_environment(docker, cid, command):
    query = command(docker + ['exec', cid, '/system/bin/pidof', 'adbd'], 10)
    pids = query.stdout.split()
    if query.returncode or len(pids) != 1 or not re.fullmatch('[0-9]{1,10}', pids[0]):
        raise ValueError('One actual Android adbd process is required')
    # Filter in Android before crossing the container boundary. Never export a
    # whole process environment or inherit host tokens into the instrumentation.
    selection = '|'.join(BOOT_KEYS)
    shell = "tr '\\000' '\\n' < /proc/" + pids[0] + "/environ | grep -E '^(" + selection + ")='"
    query = command(docker + ['exec', cid, '/system/bin/sh', '-c', shell], 10)
    if query.returncode:
        raise ValueError('Public Android boot environment is unavailable')
    values = {}
    for line in query.stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in BOOT_KEYS or key in values:
            raise ValueError('Public boot environment key differs')
        if key.endswith('CLASSPATH'):
            valid = bool(value) and all(
                re.fullmatch(r'/(?:apex|system|system_ext|vendor|product)/[A-Za-z0-9_./@+-]+\.jar', part)
                and not any(segment in ('.', '..') for segment in part.split('/'))
                for part in value.split(':'))
        else:
            valid = value in ('/system', '/data') or bool(re.fullmatch(r'/apex/com\.android\.[A-Za-z0-9_.-]+', value))
        if not valid:
            raise ValueError('Boot environment contains a non-platform value')
        values[key] = value
    if not values.get('BOOTCLASSPATH') or values.get('ANDROID_ROOT') != '/system':
        raise ValueError('Actual public Android boot classpath is required')
    return values


def component_tests(docker, cid, command, bootenv, deadline):
    parser = sibling('verify_apk_lab_results')
    prefix = docker + ['exec']
    for key, value in bootenv.items():
        prefix += ['--env', key + '=' + value]
    prefix += [cid]
    results = {}
    for stage, classname, extra, seconds in (
        ('drawable', 'HandheldDrawableAbiTest', ['-e', 'handheldDrawableAbi', 'true'], 120),
        ('runner', 'HandheldRunnerTest', [], 420),
    ):
        for package in (PACKAGE + '.test', PACKAGE):
            stopped = command(prefix + ['/system/bin/am', 'force-stop', package], 15)
            if stopped.returncode:
                raise ValueError('Owned package force-stop failed')
            until = min(deadline, time.monotonic() + 5)
            while time.monotonic() < until:
                probe = command(prefix + ['/system/bin/pidof', package], 5)
                if probe.returncode == 1 and not probe.stdout.strip():
                    break
                if probe.returncode != 0 or not re.fullmatch(r'[0-9 ]+', probe.stdout.strip()):
                    raise ValueError('Owned package PID check was not conclusive')
                time.sleep(0.2)
            else:
                raise ValueError('Owned package process survived force-stop')
        args = prefix + ['/system/bin/am', 'instrument', '-w', '-r', '-e', 'class',
                         'app.gamenative.security.' + classname] + extra + [RUNNER]
        remaining = deadline - time.monotonic() - 45
        if remaining <= 0:
            raise TimeoutError()
        output, exit_code, timed_out, output_limit = bounded_capture(args, min(seconds, remaining))
        result = parser.summarize_instrumentation(stage, output, exit_code)
        result['transport_timed_out'] = timed_out
        result['output_limit_reached'] = output_limit
        result['passed'] = result['passed'] and not timed_out and not output_limit
        results[stage] = result
        if not result['passed']:
            break
    return {'scope': 'Raw JNI1 plus Runner8, including protected generated GL; not a game/login test',
            'passed': len(results) == 2 and all(result['passed'] for result in results.values()),
            'stages': results}


def runtime_diagnostics(stage, text):
    """Export only fixed probe identities and bounded numeric fields, never text."""
    schemas = {
        'd3d': (
            ('load', r'LOAD_(DXGI|D3D11) ok=([01]) error=([0-9]{1,10})',
             ('module', 'ok', 'error'), (str, int, int)),
            ('factory', r'FACTORY attempted=([01]) hr=0x([0-9a-fA-F]{8}) object=([01])',
             ('attempted', 'hr', 'object'), (int, lambda x: int(x, 16), int)),
            ('adapter', r'ADAPTER attempted=([01]) hr=0x([0-9a-fA-F]{8}) object=([01]) '
             r'desc_attempted=([01]) desc_hr=0x([0-9a-fA-F]{8}) vendor=([0-9]{1,10}) '
             r'device=([0-9]{1,10}) flags=([0-9]{1,10})',
             ('attempted', 'hr', 'object', 'desc_attempted', 'desc_hr', 'vendor', 'device', 'flags'),
             (int, lambda x: int(x, 16), int, int, lambda x: int(x, 16), int, int, int)),
            ('device', r'(DEFAULT|EXPLICIT) attempted=([01]) hr=0x([0-9a-fA-F]{8}) '
             r'feature=0x([0-9a-fA-F]{8}) device=([01]) context=([01])',
             ('route', 'attempted', 'hr', 'feature', 'device', 'context'),
             (str, int, lambda x: int(x, 16), lambda x: int(x, 16), int, int)),
        ),
        'tls': (
            ('certificate', r'(TRUSTED|UNTRUSTED|EXPIRED|HOSTNAME|SELF_SIGNED) '
             r'status=([0-9]{1,10}) error=([0-9]{1,10}) certificate_flags=([0-9]{1,10})',
             ('case', 'status', 'error', 'certificate_flags'), (str, int, int, int)),
        ),
    }
    records = []
    matched = 0
    for line in text.splitlines():
        for kind, pattern, fields, conversions in schemas[stage]:
            match = re.fullmatch(pattern, line)
            if match:
                matched += 1
                if len(records) < 32:
                    records.append({'kind': kind, **{
                        key: conversion(value) for key, conversion, value
                        in zip(fields, conversions, match.groups())}})
                break
    markers = (
        ('WOW_D3D11_PROBE_V1', 'WOW_D3D11_PROBE_PASS', 'WOW_D3D11_PROBE_FAIL',
         'D3D_DIAGNOSTIC_UPPER_PAIR_BYTES_VERIFIED_NOT_LOADED_PE_ATTESTATION',
         'D3D_LIVE_PARTIAL_NUMERIC_SNAPSHOT_NOT_SUCCESS',
         'D3D_POST_KILL_PARTIAL_NUMERIC_SNAPSHOT_NOT_SUCCESS') if stage == 'd3d' else
        ('WINE_TLS_PASS', 'WINE_TLS_FAIL', 'WINE_TLS_EXTENDED_PASS',
         'WINE_TLS_EXTENDED_FAIL', 'HANDHELD_INSTALLED_WINE_TLS_PASS'))
    lines = text.splitlines()
    return {'numeric_records': records, 'numeric_records_truncated': matched > len(records),
            'fixed_markers_observed': {marker: marker in lines for marker in markers},
            'preparation_status_observed': any(line.startswith('Preparation: ') for line in lines),
            'session_status_observed': any(line.startswith('Session: ') for line in lines)}


def game_diagnostics(log, instrumentation):
    """Fixed preparation/gate/frame observations; no names, paths, images or text."""
    lines = log.splitlines()
    gates = []
    frames = []
    gate_count = frame_count = 0
    last_gate = None
    strict_gate_observed = False
    for line in lines:
        gate = re.fullmatch(r'Startup gate: game_windows=([0-9]{1,10}) '
                            r'game_processes=([0-9]{1,10}) mapped_classes=.*', line)
        if gate:
            gate_count += 1
            last_gate = {'game_windows': int(gate[1]), 'game_processes': int(gate[2])}
            if len(gates) < 32:
                gates.append(last_gate)
        frame = re.fullmatch(r'Captured actual game render: handheld-game-[0-9]{1,16}-([012])\.png '
                             r'([1-9][0-9]{0,4})x([1-9][0-9]{0,4})', line)
        if frame:
            frame_count += 1
            if len(frames) < 8:
                frames.append({'index': int(frame[1]), 'width': int(frame[2]), 'height': int(frame[3])})
        if re.fullmatch(r'Actual game window/process observed; waiting [0-9]{1,3}s before capture', line):
            strict_gate_observed = True
    source = log + '\n' + instrumentation
    exits = {}
    for label, pattern in (
        ('wine_exit_before_frame', r'Wine exited before a game frame: (-?[0-9]{1,10})(?![0-9])'),
        ('wine_exit_before_capture', r'Wine exited before capture: (-?[0-9]{1,10})(?![0-9])'),
    ):
        values = [int(value) for value in re.findall(pattern, source)]
        exits[label] = [value for value in values if -(2**31) <= value < 2**31][:8]
    preparation = {
        'official_build_check': 'Preparation: Checking the latest World of Warcraft build',
        'official_client_verification': 'Preparation: Verifying World of Warcraft client files',
        'official_archive_index_check': 'Preparation: Checking archive indexes (',
        'private_prefix_configuration': 'Preparation: Configuring ',
        'private_prefix_creation': 'Preparation: Creating prefix environment (first boot)',
        'rootfs_download': 'Preparation: Downloading first-run runtime',
        'rootfs_install': 'Preparation: Installing first-run runtime',
        'wine_and_graphics_preparation': 'Session: Preparing Wine and graphics',
        'application_window_callback': 'Session: Game window opened',
    }
    return {
        'preparation_phases_observed': {
            phase: any(line.startswith(prefix) for line in lines) for phase, prefix in preparation.items()},
        'baseline_production_wrapper_selected': True,
        'direct_game_diagnostic_selected': False,
        'startup_gate_samples': gates, 'startup_gate_sample_count': gate_count,
        'startup_gate_samples_truncated': gate_count > len(gates), 'last_startup_gate': last_gate,
        'strict_game_window_and_process_observed': strict_gate_observed,
        'frame_observations': frames, 'frame_observation_count': frame_count,
        'frame_observations_truncated': frame_count > len(frames),
        'fixed_wine_exit_codes': exits,
        'fixed_errors_observed': {
            'no_current_game_window_and_process': 'No current mapped WoW window and native game process' in source,
            'game_window_or_process_disappeared': 'The game window/process disappeared' in source,
            'frame_or_fixture_future_timeout': 'java.util.concurrent.TimeoutException' in source,
            'instrumentation_process_crashed': 'Process crashed' in source,
        },
    }


def runtime_test(stage, docker, cid, command, bootenv, deadline):
    """One real installed-runtime fixture; selected-tag output stays private."""
    classname, flag, tag, seconds, scope = {
        'd3d': ('HandheldD3dProbeTest', 'handheldD3dProbe', 'HandheldD3dProbe', 600,
                'Actual Wine D3D11 default-hardware and actual-adapter FL11_0 device creation; '
                'not game presentation or loaded-PE attestation'),
        'tls': ('HandheldWineTlsTest', 'handheldWineTls', 'HandheldWineTlsTest', 900,
                'Actual installed Wine certificate matrix and seven mapped-library identities; '
                'no game, login, or account input'),
        'game': ('HandheldGameFrameTest', 'handheldGameFrame', 'HandheldGameFrameTest', 2100,
                 'Baseline production game launch and actual render-path capture only; '
                 'visible login and unsent input require separate review'),
    }[stage]
    started = time.monotonic()
    result = {'stage': stage, 'scope': scope, 'passed': False, 'timings_seconds': {},
              'milestones': {'fresh_process': False, 'main_log_reset': False,
                             'instrumentation_started': False, 'selected_tag_captured': False},
              'credential_use_approved': False}
    if stage == 'game':
        result.update({'verdict_scope': 'automated_game_capture_only', 'automated_game_capture_passed': False,
                       'visual_login_review_required': True, 'visible_login_confirmed': False,
                       'dummy_input_test_completed': False, 'account_input_sent': False})
    prefix = docker + ['exec']
    for key, value in bootenv.items():
        prefix += ['--env', key + '=' + value]
    prefix += [cid]
    phase = 'fresh_process'
    try:
        before = time.monotonic()
        for package in (PACKAGE + '.test', PACKAGE):
            stopped = command(prefix + ['/system/bin/am', 'force-stop', package], 15)
            if stopped.returncode:
                raise ValueError('Owned package force-stop failed')
            until = min(deadline, time.monotonic() + 5)
            while time.monotonic() < until:
                probe = command(prefix + ['/system/bin/pidof', package], 5)
                if probe.returncode == 1 and not probe.stdout.strip():
                    break
                if probe.returncode != 0 or not re.fullmatch(r'[0-9 ]+', probe.stdout.strip()):
                    raise ValueError('Owned package PID check was not conclusive')
                time.sleep(0.2)
            else:
                raise ValueError('Owned package process survived force-stop')
        result['milestones']['fresh_process'] = True
        result['timings_seconds']['fresh_process'] = round(time.monotonic() - before, 3)
        phase = 'log_reset'
        before = time.monotonic()
        cleared = command(docker + ['exec', cid, '/system/bin/logcat', '-b', 'main', '-c'], 10)
        if cleared.returncode:
            raise ValueError('Fresh selected-log boundary unavailable')
        result['milestones']['main_log_reset'] = True
        result['timings_seconds']['log_reset'] = round(time.monotonic() - before, 3)
        result['platform_processes_before_fixture'] = platform_process_snapshot(docker, cid, command)
        remaining = deadline - time.monotonic() - 45
        if remaining <= 0:
            raise TimeoutError()
        phase = 'instrumentation'
        before = time.monotonic()
        result['milestones']['instrumentation_started'] = True
        output, exit_code, timed_out, output_limit = bounded_capture(
            prefix + ['/system/bin/am', 'instrument', '-w', '-r', '-e', 'class',
                      'app.gamenative.security.' + classname, '-e', flag, 'true', RUNNER],
            min(seconds, remaining))
        result['timings_seconds']['instrumentation'] = round(time.monotonic() - before, 3)
        result.update({'instrumentation_exit_code': exit_code, 'transport_timed_out': timed_out,
                       'output_limit_reached': output_limit})
        phase = 'selected_tag_capture'
        before = time.monotonic()
        remaining = deadline - before - 25
        if remaining <= 0:
            raise TimeoutError()
        log, log_exit, log_timeout, log_limit = bounded_capture(
            docker + ['exec', cid, '/system/bin/logcat', '-b', 'main', '-d', '-v', 'raw',
                      '-s', tag + ':I', '*:S'], min(15, remaining))
        result['timings_seconds']['selected_tag_capture'] = round(time.monotonic() - before, 3)
        log_complete = log_exit == 0 and not log_timeout and not log_limit
        result['milestones']['selected_tag_captured'] = log_complete
        result.update({'selected_log_exit_code': log_exit, 'selected_log_timed_out': log_timeout,
                       'selected_log_output_limit_reached': log_limit})
        phase = 'result_verification'
        # This specialized verifier is mandatory; JUnit completion alone is insufficient.
        verified = sibling('verify_apk_lab_results').summarize(stage, output, log, exit_code=exit_code)
        result['verification'] = verified
        result['diagnostics'] = (game_diagnostics(log, output) if stage == 'game' else
                                 runtime_diagnostics(stage, log))
        result['passed'] = verified['passed'] and not timed_out and not output_limit and log_complete
        if stage == 'game':
            result['automated_game_capture_passed'] = result['passed']
        if not result['passed']:
            result['failure_phase'] = 'result_verification'
    except Exception as failure:
        result['failure_phase'] = phase
        result['error_class'] = type(failure).__name__ if type(failure).__name__ in (
            'ValueError', 'TimeoutError', 'TimeoutExpired', 'OSError', 'RuntimeError') else 'OtherError'
    if result['milestones']['instrumentation_started'] and not result['passed'] and deadline - time.monotonic() >= 30:
        result['platform_processes_after_fixture'] = platform_process_snapshot(docker, cid, command)
        try:
            result['system_log_after_failed_fixture'] = sibling('android_boot_diagnostics').collect_boot_log(
                docker, cid, bounded_capture, after_fixture=True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            result['system_log_after_failed_fixture'] = {'read_complete': False}
    result['timings_seconds']['total'] = round(time.monotonic() - started, 3)
    return result


def prepare_cache_overlay(docker, image, expected_image_id, work, owner, command):
    """Read one pinned public file from an owned container that is never started."""
    name = 'wow-arm64-properties-' + owner[:16]
    cidfile = work / 'properties.cid'
    cid = None

    def owned(identity, cleanup=False):
        argv = docker + ['inspect', identity]
        value = (subprocess.run(argv, capture_output=True, text=True, timeout=15)
                 if cleanup else command(argv, 15))
        if value.returncode:
            raise RuntimeError('Public property capture identity unavailable')
        metadata = json.loads(value.stdout)[0]
        assert metadata['Id'] == identity and metadata['Name'] == '/' + name
        assert metadata['Config']['Image'] == image and metadata['Image'] == expected_image_id
        assert metadata['Config']['Labels'].get('io.wowforever.property-owner') == owner
        assert not metadata['State']['Running'] and metadata['State']['Pid'] == 0
        assert metadata['HostConfig']['NetworkMode'] == 'none' and not metadata['HostConfig']['PortBindings']
        assert not any(row['Type'] == 'bind' for row in metadata['Mounts'])
        assert (work / '.owned-arm64-boot').read_text() == owner

    def checked(argv, seconds=20):
        result = command(argv, seconds)
        if result.returncode:
            raise RuntimeError('Public property capture failed')
        return result

    try:
        checked(docker + ['create', '--pull=never', '--platform=linux/arm64', '--network=none',
                         '--name', name, '--cidfile', str(cidfile), '--label',
                         'io.wowforever.property-owner=' + owner, image], 30)
        cid = cidfile.read_text().strip()
        assert re.fullmatch('[0-9a-f]{64}', cid)
        owned(cid)
        original = work / 'original-system-build.prop'
        checked(docker + ['cp', cid + ':/system/build.prop', str(original)], 20)
        assert original.is_file() and not original.is_symlink()
        base = original.read_bytes()
        assert len(base) == 5231 and hashlib.sha256(base).hexdigest() == (
            '38a385a1ae931aabc0e4950a506c1f3eeeb84f141ad0a2620b2223183845082e')
        assert base.endswith(b'\n') and b'service.sf.prime_shader_cache' not in base
        raw = base + b'service.sf.prime_shader_cache=false\n'
        expected = 'f76133a2f65993637c86b1694bbc646aa5fc337238bda6083ac28750d535d4f2'
        assert len(raw) == 5267 and hashlib.sha256(raw).hexdigest() == expected
        path = work / 'cache-system-build.prop'
        with path.open('xb') as target:
            target.write(raw)
        path.chmod(0o444)
        assert path.read_bytes() == raw and path.stat().st_mode & 0o222 == 0
        return {'source': str(path), 'destination': '/system/build.prop', 'sha256': expected}
    finally:
        if cid is None and cidfile.is_file():
            candidate = cidfile.read_text().strip()
            if re.fullmatch('[0-9a-f]{64}', candidate):
                cid = candidate
        if cid is not None:
            owned(cid, cleanup=True)
            removed = subprocess.run(docker + ['rm', cid], capture_output=True, timeout=20)
            if removed.returncode:
                raise RuntimeError('Owned public property capture container cleanup failed')


def platform_process_snapshot(docker, cid, command):
    """Best-effort public process identities only; never a readiness oracle."""
    result = {}
    for name in ('surfaceflinger', 'system_server'):
        try:
            value = command(docker + ['exec', cid, '/system/bin/pidof', name], 2)
            pid = value.stdout.strip()
            if value.returncode == 1 and not pid:
                result[name] = {'status': 'absent'}
                continue
            if value.returncode or not re.fullmatch(r'[1-9][0-9]{0,9}', pid):
                result[name] = {'status': 'unavailable'}
                continue
            value = command(docker + ['exec', cid, '/system/bin/cat', '/proc/' + pid + '/stat'], 2)
            prefix = pid + ' (' + name + ') '
            if value.returncode or not value.stdout.startswith(prefix):
                result[name] = {'status': 'unavailable'}
                continue
            fields = value.stdout[len(prefix):].split()
            if len(fields) <= 19 or not re.fullmatch(r'[0-9]{1,20}', fields[19]):
                result[name] = {'status': 'unavailable'}
                continue
            result[name] = {'status': 'observed', 'pid': int(pid), 'start_ticks': int(fields[19])}
        except Exception:
            result[name] = {'status': 'unavailable'}
    return result


def assert_no_data_mounters(docker, data, command):
    """Do not start a second reader/writer of this fresh, runner-owned data tree."""
    listed = command(docker + ['ps', '--no-trunc', '-q'], 10)
    identities = listed.stdout.split()
    assert listed.returncode == 0 and len(identities) <= 64
    assert all(re.fullmatch(r'[0-9a-f]{64}', value) for value in identities)
    if not identities:
        return
    inspected = command(docker + ['inspect'] + identities, 15)
    assert inspected.returncode == 0
    for metadata in json.loads(inspected.stdout):
        if not metadata['State']['Running']:
            continue
        for mount in metadata['Mounts']:
            if mount['Type'] != 'bind':
                continue
            path = Path(mount['Source']).resolve()
            assert path != data and path not in data.parents and data not in path.parents


def host_veth_state():
    try:
        setting = 'set' if Path('/proc/sys/kernel/modprobe').read_bytes().strip() else 'empty'
    except OSError:
        setting = 'unavailable'
    return {'kernel_modprobe_setting': setting, 'veth_present': Path('/sys/module/veth').is_dir()}


def installed_component_stage(docker, cid, command, bootenv, hal, args, deadline, install_progress=None):
    """Actual fixed input installs and platform loader proof before a selected stage."""
    progress = install_progress if install_progress is not None else {}
    progress.update({'phase': 'namespace', 'packages': {}, 'hal_files_verified': 0})
    def checked_android(values, seconds=20):
        value = command(docker + ['exec', cid] + values, seconds)
        if value.returncode:
            raise ValueError('Owned Android prerequisite failed')
        return value.stdout.strip()
    expected_namespace = 'bc09359d857bcd4a9028ee0bb2e0bfd99f8bb74de2c1c4ffe67fa00aff2620b1'
    namespace = checked_android(['/system/bin/sha256sum', '/linkerconfig/ld.config.txt'])
    if namespace.split()[0] != expected_namespace:
        raise ValueError('Public Android namespace differs from reviewed closure')
    progress['namespace_verified'] = True
    progress['phase'] = 'vulkan_selection'
    if checked_android(['/system/bin/getprop', 'ro.hardware.vulkan']) != 'pastel':
        raise ValueError('Selected system Vulkan HAL differs')
    progress['vulkan_selection_verified'] = True
    progress['phase'] = 'build_property_digest'
    expected_properties = ('f76133a2f65993637c86b1694bbc646aa5fc337238bda6083ac28750d535d4f2' if hal else
                           '38a385a1ae931aabc0e4950a506c1f3eeeb84f141ad0a2620b2223183845082e')
    if checked_android(['/system/bin/sha256sum', '/system/build.prop']).split()[0] != expected_properties:
        raise ValueError('Public selected-profile boot properties differ')
    progress['build_property_digest_verified'] = True
    if hal:
        progress['phase'] = 'cache_property_readback'
        if checked_android(['/system/bin/getprop', 'service.sf.prime_shader_cache']) != 'false':
            raise ValueError('Reviewed optional shader-cache setting differs')
        progress['cache_property_verified'] = True
        progress['phase'] = 'mapped_hal_digest'
        sf = checked_android(['/system/bin/pidof', 'surfaceflinger'])
        if not re.fullmatch('[0-9]{1,10}', sf):
            raise ValueError('One actual SurfaceFlinger is required')
        maps = checked_android(['/system/bin/cat', '/proc/' + sf + '/maps'])
        paths = {parts[5] for line in maps.splitlines()
                 if len(parts := line.split(None, 5)) == 6 and 'x' in parts[1]}
        for entry in hal['readonly_bind_targets']:
            actual = checked_android(['/system/bin/sha256sum', entry['destination']])
            if actual.split()[0] != entry['sha256'] or entry['destination'] not in paths:
                raise ValueError('Actual mapped public HAL bytes differ')
            progress['hal_files_verified'] += 1
    prefix = docker + ['exec']
    for key, value in bootenv.items():
        prefix += ['--env', key + '=' + value]
    prefix += [cid]
    installed = {}
    for package, source, expected in ((PACKAGE, args.main, MAIN_SHA), (PACKAGE + '.test', args.qa, QA_SHA)):
        progress['package'] = package
        progress['packages'][package] = {}
        progress['phase'] = 'apk_copy'
        remote = '/data/local/tmp/' + ('main.apk' if package == PACKAGE else 'qa.apk')
        copied = command(docker + ['cp', str(source.resolve()), cid + ':' + remote], 60)
        if copied.returncode:
            raise ValueError('Owned APK copy failed')
        progress['packages'][package]['copy_succeeded'] = True
        progress['phase'] = 'package_manager_install'
        result = command(prefix + ['/system/bin/pm', 'install', '-r', '-t', remote], 120)
        if result.returncode or result.stdout.strip() != 'Success':
            raise ValueError('Package Manager did not confirm exact APK install')
        progress['packages'][package]['package_manager_success'] = True
        progress['phase'] = 'installed_base_path'
        query = command(prefix + ['/system/bin/pm', 'path', package], 20)
        lines = query.stdout.strip().splitlines()
        if query.returncode or len(lines) != 1 or not re.fullmatch(r'package:/data/app/[A-Za-z0-9_/=+~.-]+/base\.apk', lines[0]):
            raise ValueError('Installed package base path is not unique')
        base = lines[0][len('package:'):]
        progress['phase'] = 'installed_base_digest'
        if checked_android(['/system/bin/sha256sum', base]).split()[0] != expected:
            raise ValueError('Installed base APK bytes differ')
        installed[package] = expected
        progress['packages'][package]['installed_base_digest_verified'] = True
    progress['phase'] = 'complete'
    result = (component_tests(docker, cid, command, bootenv, deadline) if args.stage == 'native' else
              runtime_test(args.stage, docker, cid, command, bootenv, deadline))
    result['installed_apk_sha256'] = installed
    result['public_namespace_sha256'] = expected_namespace
    result['nine_public_hal_files_hash_checked_and_mapped'] = bool(hal)
    result['graphics_profile'] = 'stock_warm_lavapipe' if hal else 'stock'
    result['native_bridge_override_or_env_mirroring_used'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', required=True, type=Path)
    parser.add_argument('--qa', required=True, type=Path)
    parser.add_argument('--hal', required=True, type=Path)
    parser.add_argument('--stage', choices=('native', 'd3d', 'tls', 'game', 'all'), default='native')
    args = parser.parse_args()
    if args.stage not in ('native', 'd3d', 'tls', 'game'):
        print(json.dumps({'passed': False, 'scope': 'Requested later stage is not implemented; no lab started'}))
        return 2
    verify_input(args.main, 129571767, MAIN_SHA)
    verify_input(args.qa, 7403478, QA_SHA)
    verify_input(args.hal, 52218250, HAL_SHA)
    return run_lab(args)


# The reviewed BinderFS/image/bootstrap body is appended by the source preparation
# step. Importing this file performs no mounts, module loads, downloads or tests.


def run_lab(args):
    lab_args = args
    image = 'redroid/redroid@sha256:b21c57533662275928832602f7c09b83cb198aa9b271135ae49d978857d6795f'
    expected_image_id = 'sha256:c9c5cfdc4671d32340b5305f623e286c29da971b5f22d382e78829c9282a16d0'
    owner = uuid.uuid4().hex
    base_name = 'wow-arm64-boot-' + owner[:16]
    name = base_name
    work = Path(tempfile.mkdtemp(prefix='wow-arm64-boot-', dir='/tmp')).resolve()
    (work / '.owned-arm64-boot').write_text(owner)
    data = work / 'data'
    data.mkdir(mode=0o700)
    data_identity = (data.stat().st_dev, data.stat().st_ino)
    cidfile = work / 'container.cid'
    binderfs = work / 'binderfs'
    binderfs.mkdir(mode=0o700)
    binder_mount_id = None
    binder_mounted = False
    empty_config = '/proc/self/arm64-preflight-no-client-config'
    assert not Path(empty_config).exists()
    docker = [shutil.which('docker'), '--config', empty_config, '--host', 'unix:///var/run/docker.sock']
    started = time.monotonic()
    deadline = started + (2700 if lab_args.stage == 'game' else 1500)
    cid = None
    report = {'scope': ('Native ARM64 JNI and protected generated GL only; not a game/login/credential verdict'
                        if lab_args.stage == 'native' else
                        ('Baseline production game capture; visual login and unsent input are not verified'
                         if lab_args.stage == 'game' else
                         'Selected actual installed Wine ' + lab_args.stage + ' stage; not a game/login/credential verdict')),
              'selected_stage': lab_args.stage,
              'boot_only_pass': False, 'passed': False, 'hal_overlays': False, 'boot_profiles': [], 'account_input_sent': False,
              'adb_published': False, 'owned_container_removed': False,
              'owned_binderfs_unmounted': False, 'phase': 'kernel_modules'}

    def command(args, seconds=20):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return subprocess.run(args, capture_output=True, text=True, timeout=min(seconds, remaining))

    def checked(args, seconds=20):
        value = command(args, seconds)
        if value.returncode:
            raise RuntimeError('Boot prerequisite command failed')
        return value.stdout

    def owned_container(identity, cleanup=False):
        runner = subprocess.run if cleanup else command
        if cleanup:
            inspected = runner(docker + ['inspect', identity], capture_output=True, text=True, timeout=15)
        else:
            inspected = runner(docker + ['inspect', identity], 15)
        if inspected.returncode:
            raise RuntimeError('Owned container identity unavailable')
        value = json.loads(inspected.stdout)[0]
        assert value['Id'] == identity and value['Name'] == '/' + name
        assert value['Config']['Image'] == image
        assert value['Image'] == expected_image_id
        assert value['Config']['Labels'].get('io.wowforever.boot-owner') == owner
        mounts = value['Mounts']
        selected = [row for row in mounts if row['Destination'] == '/data']
        assert len(selected) == 1 and selected[0]['Source'] == str(data)
        assert (work / '.owned-arm64-boot').read_text() == owner
        return value

    def owned_binder_mount():
        assert work.parent == Path('/tmp') and work.name.startswith('wow-arm64-boot-')
        assert (work / '.owned-arm64-boot').read_text() == owner
        assert binderfs.parent == work and not binderfs.is_symlink()
        rows = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            if len(fields) > 6 and fields[4] == str(binderfs):
                assert fields[fields.index('-') + 1] == 'binder'
                rows.append(int(fields[0]))
        assert len(rows) <= 1
        return rows[0] if rows else None

    def create_and_boot(profile_report, network, overlays, readiness_seconds):
        nonlocal cid, cidfile, name
        name = base_name + '-' + profile_report['graphics_profile']
        cidfile = work / (profile_report['graphics_profile'] + '.cid')
        cid = None
        report['owned_container_removed'] = False
        last_identity_sample = [float('-inf')]
        previous_identities = {}
        profile_report['platform_process_samples'] = []
        profile_report['platform_process_identity_changes'] = {'surfaceflinger': 0, 'system_server': 0}

        def observe_processes():
            snapshot = platform_process_snapshot(docker, cid, command)
            last_identity_sample[0] = time.monotonic()
            entry = {'elapsed_seconds': round(last_identity_sample[0] - started, 3), 'identities': snapshot}
            if len(profile_report['platform_process_samples']) < 32:
                profile_report['platform_process_samples'].append(entry)
            profile_report['last_platform_process_sample'] = entry
            for key in ('surfaceflinger', 'system_server'):
                before, after = previous_identities.get(key), snapshot[key]
                if before and before['status'] == after['status'] == 'observed' and before != after:
                    profile_report['platform_process_identity_changes'][key] += 1
                previous_identities[key] = after

        profile_report['phase'] = 'container_create'
        args = docker + ['create', '--pull=never', '--platform=linux/arm64', '--name', name,
            '--cidfile', str(cidfile), '--label', 'io.wowforever.boot-owner=' + owner,
            '--privileged', '--network=' + network, '--cpus=4', '--memory=8g', '--memory-swap=8g',
            '--mount', 'type=bind,src=' + str(data) + ',dst=/data']
        for node in ('binder', 'hwbinder', 'vndbinder'):
            args += ['--mount', 'type=bind,src=' + str(binderfs / node) + ',dst=/dev/' + node]
        for entry in overlays:
            args += ['--mount', 'type=bind,src=' + entry['source'] + ',dst=' + entry['destination'] + ',readonly']
        args += [image, 'androidboot.use_memfd=1', 'androidboot.redroid_width=540',
                 'androidboot.redroid_height=960', 'androidboot.redroid_dpi=240',
                 'androidboot.redroid_gpu_mode=guest']
        checked(args, 30)
        cid = cidfile.read_text().strip()
        assert re.fullmatch('[0-9a-f]{64}', cid)
        meta = owned_container(cid)
        assert meta['HostConfig']['Memory'] == 8589934592
        assert meta['HostConfig']['MemorySwap'] == 8589934592
        assert meta['HostConfig']['NanoCpus'] == 4000000000
        assert meta['HostConfig']['NetworkMode'] == network
        assert not meta['HostConfig']['PortBindings']
        mounts = {row['Destination']: row for row in meta['Mounts']}
        for entry in overlays:
            row = mounts[entry['destination']]
            assert row['Type'] == 'bind' and row['RW'] is False and row['Source'] == entry['source']
        assert set(mounts) == {'/data', '/dev/binder', '/dev/hwbinder', '/dev/vndbinder'} | {entry['destination'] for entry in overlays}
        report['hal_overlays'] = bool(overlays)
        profile_report['phase'] = 'container_start'
        profile_report['host_network_before_start'] = host_veth_state()
        launch = command(docker + ['start', cid], 30)
        if launch.returncode:
            targets = ['/data', '/dev/binder', '/dev/hwbinder', '/dev/vndbinder']
            targets.extend(entry['destination'] for entry in overlays)
            diagnostics = sibling('android_boot_diagnostics')
            failure = diagnostics.docker_start_failure(launch.stderr, launch.returncode, targets)
            profile_report['docker_start_failure'] = failure
            profile_report['binder_devices_still_present'] = {
                node: (binderfs / node).is_char_device() for node in ('binder', 'hwbinder', 'vndbinder')}
            if network == 'bridge' and diagnostics.can_retry_with_veth(failure, host_veth_state()):
                # The stock Android init may turn off module autoloading before
                # Docker creates the first veth pair on this disposable runner.
                module = command([modinfo, '-F', 'filename', 'veth'], 10)
                recovery = {'veth_module_available': module.returncode == 0, 'retried_container_start': False}
                profile_report['veth_recovery'] = recovery
                if recovery['veth_module_available']:
                    loaded = command([modprobe, 'veth'], 10)
                    recovery['modprobe_exit'] = loaded.returncode
                    recovery['host_state_after_modprobe'] = host_veth_state()
                    if loaded.returncode == 0 and recovery['host_state_after_modprobe']['veth_present']:
                        state = owned_container(cid)['State']
                        assert not state['Running'] and state['Pid'] == 0
                        launch = command(docker + ['start', cid], 30)
                        recovery['retried_container_start'] = True
                        recovery['retry_exit_code'] = launch.returncode
                        if launch.returncode:
                            recovery['retry_failure'] = diagnostics.docker_start_failure(launch.stderr, launch.returncode, targets)
            if launch.returncode:
                raise RuntimeError('Owned Android container start failed')
        profile_report['owned_container_started'] = True
        observe_processes()
        profile_report['phase'] = 'boot_wait'
        boot_deadline = min(deadline - 30, time.monotonic() + 300)
        boot = ''
        while time.monotonic() < boot_deadline:
            meta = owned_container(cid)
            if not meta['State']['Running']:
                profile_report['container_exit_code'] = meta['State']['ExitCode']
                break
            try:
                query = command(docker + ['exec', cid, '/system/bin/getprop', 'sys.boot_completed'], 10)
                if query.returncode == 0 and query.stdout.strip() == '1':
                    boot = '1'
                    observe_processes()
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(3)
        profile_report['boot_completed'] = boot == '1'
        profile_report['phase'] = 'readiness'
        properties = {}
        for key in ('ro.build.version.sdk', 'ro.product.cpu.abi', 'ro.dalvik.vm.native.bridge'):
            query = command(docker + ['exec', cid, '/system/bin/getprop', key], 10)
            value = query.stdout.strip() if query.returncode == 0 else ''
            properties[key] = value if re.fullmatch('[A-Za-z0-9._-]{0,64}', value) else 'unclassified'
        profile_report['public_properties'] = properties
        services = {}
        for service in ('window', 'SurfaceFlinger'):
            query = command(docker + ['exec', cid, '/system/bin/service', 'check', service], 10)
            services[service] = query.returncode == 0 and query.stdout.strip() == 'Service ' + service + ': found'
        profile_report['public_services_found'] = services
        readiness_deadline = min(deadline - 30, time.monotonic() + readiness_seconds)
        profile_report['readiness_samples'] = []
        profile_report['wms_responsive'] = profile_report['display_enabled'] = profile_report['system_booted'] = profile_report['display_ready'] = False

        def readiness_query(args, seconds=10):
            remaining = readiness_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            return command(docker + ['exec', cid] + args, min(seconds, remaining))

        def public_boolean(text, key):
            values = set(re.findall(r'\b' + re.escape(key) + r'=(true|false)\b', text))
            return next(iter(values)) if len(values) == 1 else ('mixed' if values else 'unavailable')

        while time.monotonic() < readiness_deadline:
            before_wms = time.monotonic()
            query = readiness_query(['/system/bin/timeout', '10', '/system/bin/dumpsys', 'window', '-a'], 15)
            profile_report['wms_query_seconds'] = round(time.monotonic() - before_wms, 3)
            profile_report['wms_query_exit'] = query.returncode
            profile_report['wms_responsive'] = query.returncode == 0 and 'WINDOW MANAGER' in query.stdout
            profile_report['display_enabled'] = 'mDisplayEnabled=true' in query.stdout
            profile_report['system_booted'] = 'mSystemBooted=true' in query.stdout
            private_display = public_boolean(query.stdout, 'mDisplayEnabled')
            secure_override = public_boolean(query.stdout, 'mDisableSecureWindows')
            profile_report['secure_window_override_observation'] = secure_override
            profile_report['secure_window_override_disabled'] = (secure_override == 'false'
                if secure_override in ('true', 'false') else None)
            display_exit = state = valid = default_on = None
            rows = []
            if private_display == 'unavailable':
                display = readiness_query(['/system/bin/cmd', 'display', 'get-displays'])
                display_exit = display.returncode
                rows = [line for line in display.stdout.splitlines() if re.match(r'^\s*Display id 0: ', line)]
                state, valid = 'unavailable', False
                if display_exit == 0 and len(rows) == 1:
                    value = re.search(r', state (UNKNOWN|OFF|ON|DOZE|DOZE_SUSPEND|VR|ON_SUSPEND)(?=[, }])', rows[0])
                    validity = re.search(r', isValid=(true|false)\s*$', rows[0])
                    state = value[1] if value else 'unclassified'
                    valid = validity is not None and validity[1] == 'true'
                default_on = display_exit == 0 and len(rows) == 1 and valid and state == 'ON'
            profile_report['default_display_on'] = default_on
            profile_report['private_display_enabled_observation'] = private_display
            profile_report['display_ready'] = private_display == 'true' or (
                private_display == 'unavailable' and profile_report['default_display_on'])
            profile_report['display_readiness_source'] = ('verbose_wms' if private_display == 'true' else
                ('public_default_display_on' if profile_report['display_ready'] else 'unavailable_or_not_ready'))
            sample = {'elapsed_seconds': round(time.monotonic() - started, 3),
                      'wms_exit': query.returncode, 'wms_responsive': profile_report['wms_responsive'],
                      'wms_seconds': profile_report['wms_query_seconds'], 'display_query_exit': display_exit,
                      'default_display_id': 0 if len(rows) == 1 else None,
                      'default_display_state': state, 'default_display_valid': valid,
                      'default_display_row_count': len(rows) if display_exit is not None else None,
                      'fixed_wms_flags': {
                          key: public_boolean(query.stdout, key) for key in (
                              'mDisplayEnabled', 'mSystemBooted', 'mBootAnimationStopped',
                              'mDisplayFrozen', 'mDisplayReady', 'mDisableSecureWindows')}}
            profile_report['readiness_samples'].append(sample)
            if (profile_report['wms_responsive'] and profile_report['display_ready'] and profile_report['system_booted']
                    and secure_override not in ('true', 'mixed')):
                sample['optional_diagnostics_skipped_after_readiness'] = True
                break
            if time.monotonic() - last_identity_sample[0] >= 15:
                observe_processes()
            for key, allowed in (
                ('service.bootanim.exit', ('', '0', '1')),
                ('init.svc.bootanim', ('', 'running', 'stopped', 'restarting', 'stopping')),
            ):
                prop = readiness_query(['/system/bin/getprop', key])
                value = prop.stdout.strip() if prop.returncode == 0 else 'unavailable'
                sample[key] = value if value in allowed else 'unclassified'
            power = readiness_query(['/system/bin/timeout', '8', '/system/bin/dumpsys', 'power'], 12)
            sample['power_query_exit'] = power.returncode
            wakefulness = re.search(r'\bmWakefulness=(Awake|Asleep|Dreaming|Dozing)\b', power.stdout)
            screen = re.search(r'\bDisplay Power:\s*state=(ON|OFF|DOZE|DOZE_SUSPEND|VR|ON_SUSPEND)\b', power.stdout)
            sample['power_wakefulness'] = wakefulness[1] if wakefulness else 'unavailable'
            sample['power_screen_state'] = screen[1] if screen else 'unavailable'
            time.sleep(min(3, max(0, readiness_deadline - time.monotonic())))
        meta = owned_container(cid)
        profile_report['container_running'] = meta['State']['Running']
        profile_report['container_oom_killed'] = meta['State']['OOMKilled']
        profile_report['boot_only_pass'] = (meta['State']['Running'] and not meta['State']['OOMKilled'] and boot == '1' and properties == {
            'ro.build.version.sdk': '35', 'ro.product.cpu.abi': 'arm64-v8a',
            'ro.dalvik.vm.native.bridge': '0'} and all(services.values()) and
            profile_report['wms_responsive'] and profile_report['display_ready'] and profile_report['system_booted'] and
            profile_report.get('secure_window_override_observation') not in ('true', 'mixed'))
        if not profile_report['boot_only_pass']:
            raise RuntimeError('Actual platform readiness failed')

    def boot_profile(profile, network, overlays, readiness_seconds):
        profile_report = {'graphics_profile': profile, 'network': network, 'boot_only_pass': False,
                          'readiness_budget_seconds': readiness_seconds,
                          'readonly_overlay_count': len(overlays)}
        report['boot_profiles'].append(profile_report)
        report['phase'] = profile + '_boot'
        try:
            create_and_boot(profile_report, network, overlays, readiness_seconds)
        finally:
            if not profile_report['boot_only_pass'] and cid is not None:
                try:
                    metadata = owned_container(cid)
                    if metadata['State']['Running']:
                        profile_report['failure_log_diagnostics'] = sibling('android_boot_diagnostics').collect_boot_log(
                            docker, cid, bounded_capture)
                except Exception:
                    profile_report['failure_log_diagnostics'] = {'complete': False, 'error_class': 'Unavailable'}
        return profile_report

    def stop_empty_stock(profile_report):
        nonlocal cid, cidfile
        assert profile_report['graphics_profile'] == 'stock' and profile_report['boot_only_pass']
        metadata = owned_container(cid)
        assert metadata['State']['Running']
        # Observe the actual guest paths, so Android's absolute /data symlinks stay
        # inside the container rather than being resolved against the host root.
        for package in (PACKAGE, PACKAGE + '.test'):
            for root in ('/data/user/0/', '/data/user_de/0/'):
                for flag in ('-e', '-L'):
                    absent = command(docker + ['exec', cid, '/system/bin/test', flag, root + package], 5)
                    assert absent.returncode == 1 and not absent.stdout.strip()
        assert (data.stat().st_dev, data.stat().st_ino) == data_identity
        checked(docker + ['stop', '--time', '10', cid], 25)
        metadata = owned_container(cid)
        assert not metadata['State']['Running'] and metadata['State']['Pid'] == 0
        assert metadata['State']['Status'] == 'exited'
        profile_report['stopped_with_pid_zero'] = True
        checked(docker + ['rm', cid], 20)
        profile_report['owned_stock_container_removed'] = True
        profile_report['target_app_state_absent_before_transition'] = True
        cid = None
        cidfile = None  # Retire only the removed stock CID; do not recover it during later cleanup.
        report['owned_container_removed'] = True
        assert (data.stat().st_dev, data.stat().st_ino) == data_identity
        assert_no_data_mounters(docker, data, command)
        report['sequential_data_transition'] = {'same_fresh_owned_data_identity': True,
                                              'target_app_state_absent': True,
                                              'running_overlapping_data_mounters': 0}

    try:
        assert os.geteuid() == 0 and platform.machine() == 'aarch64'
        assert os.sysconf('SC_PAGE_SIZE') == 4096
        assert docker[0] is not None
        hal = (sibling('receive_public_hal').receive(lab_args.hal, work / 'hal')
               if lab_args.stage in ('d3d', 'game') else None)
        modprobe = shutil.which('modprobe')
        modinfo = shutil.which('modinfo')
        assert modprobe is not None and modinfo is not None
        for module in ('binder_linux', 'loop', 'dm_mod'):
            assert command([modinfo, '-F', 'filename', module]).returncode == 0
        checked([modprobe, 'binder_linux'])
        checked([modprobe, 'loop'])
        checked([modprobe, 'dm_mod'])
        report['phase'] = 'binder_uapi'
        build = Path('/lib/modules/' + platform.release() + '/build').resolve()
        uts = (build / 'include/generated/utsrelease.h').read_text()
        assert re.findall(r'^#define UTS_RELEASE "([^"]+)"$', uts, re.M) == [platform.release()]
        header_roots = [build]
        source_link = Path('/lib/modules/' + platform.release() + '/source')
        if source_link.is_dir():
            header_roots.append(source_link.resolve())
        for include in re.findall(r'^include\s+(/\S+)/Makefile\s*$', (build / 'Makefile').read_text(), re.M):
            included = Path(include).resolve()
            assert included.is_relative_to(Path('/usr/src'))
            header_roots.append(included)
        headers = [base / 'include/uapi/linux/android/binderfs.h' for base in header_roots]
        available = [path for path in headers if path.is_file()]
        report['matching_kernel_binder_uapi_present'] = bool(available)
        assert available
        header = available[0].resolve()
        header_bytes = header.read_bytes()
        report['public_binder_uapi_sha256'] = hashlib.sha256(header_bytes).hexdigest()
        # Copy only the exact BinderFS contract. Other includes must use the distro's
        # exported userspace headers, never the unexported kernel include/uapi tree.
        own_include = work / 'uapi-include'
        own_android = own_include / 'linux/android'
        own_android.mkdir(parents=True)
        own_header = own_android / 'binderfs.h'
        own_header.write_bytes(header_bytes)
        assert own_header.read_bytes() == header_bytes
        compiler = shutil.which('cc')
        assert compiler is not None
        source = work / 'binderfs-create.c'
        helper = work / 'binderfs-create'
        source.write_text(r'''
    #include <errno.h>
    #include <fcntl.h>
    #include <stddef.h>
    #include <stdio.h>
    #include <string.h>
    #include <sys/ioctl.h>
    #include <unistd.h>
    #include <linux/android/binderfs.h>
    _Static_assert(BINDERFS_MAX_NAME == 255, "binder name ABI");
    _Static_assert(sizeof(struct binderfs_device) == 264, "binder device ABI");
    _Static_assert(offsetof(struct binderfs_device, major) == 256, "binder major ABI");
    _Static_assert(offsetof(struct binderfs_device, minor) == 260, "binder minor ABI");
    _Static_assert(_IOC_TYPE(BINDER_CTL_ADD) == 'b' && _IOC_NR(BINDER_CTL_ADD) == 1, "binder ioctl ABI");
    _Static_assert(_IOC_SIZE(BINDER_CTL_ADD) == sizeof(struct binderfs_device), "binder ioctl size");
    _Static_assert(_IOC_DIR(BINDER_CTL_ADD) == (_IOC_READ | _IOC_WRITE), "binder ioctl direction");
    int main(int argc, char **argv) {
        const char *names[] = {"binder", "hwbinder", "vndbinder"};
        if (argc != 2) return 64;
        int fd = open(argv[1], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) return 65;
        for (unsigned i = 0; i < 3; ++i) {
            struct binderfs_device device = {0};
            memcpy(device.name, names[i], strlen(names[i]) + 1);
            if (ioctl(fd, BINDER_CTL_ADD, &device) < 0) {
                printf("ERROR %u %d\n", i, errno);
                close(fd);
                return 66;
            }
            printf("NODE %s %u %u\n", names[i], device.major, device.minor);
        }
        return close(fd) == 0 ? 0 : 67;
    }
    ''')
        compiled = command([compiler, '-std=c11', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror', '-O2',
                            '-I', str(own_include), str(source), '-o', str(helper)], 30)
        report['binder_helper_compile_exit'] = compiled.returncode
        diagnostic = (compiled.stdout + compiled.stderr)[:65536].lower()
        patterns = {
            'unexported_kernel_header_warning': r'kernel headers from user space|__exported_headers__',
            'static_abi_assertion': r'static assertion failed|static_assert',
            'missing_header': r'fatal error:.*(?:no such file|file not found)',
            'warning_as_error': r'all warnings being treated as errors|warnings treated as errors|\[-werror',
            'compiler_error': r'\berror:',
        }
        report['binder_helper_compile_fixed_classes'] = {
            key: bool(re.search(pattern, diagnostic)) for key, pattern in patterns.items()}
        report['binder_helper_compile_unclassified_failure'] = compiled.returncode != 0 and not any(
            report['binder_helper_compile_fixed_classes'].values())
        assert compiled.returncode == 0
        report['matching_uapi_helper_compiled'] = True
        report['phase'] = 'binder_mount'
        assert owned_binder_mount() is None
        checked(['mount', '-t', 'binder', 'binder', str(binderfs)])
        binder_mounted = True
        binder_mount_id = owned_binder_mount()
        assert binder_mount_id is not None
        control = binderfs / 'binder-control'
        report['binder_control_character_device'] = stat.S_ISCHR(control.lstat().st_mode)
        assert report['binder_control_character_device']
        report['phase'] = 'binder_allocate'
        allocation = command([str(helper), str(control)], 15)
        report['binder_helper_exit'] = allocation.returncode
        for line in allocation.stdout.splitlines():
            error = re.fullmatch(r'ERROR ([0-2]) ([0-9]{1,5})', line)
            if error:
                report['binder_helper_error_device_index'] = int(error[1])
                report['binder_helper_errno'] = int(error[2])
        assert allocation.returncode == 0
        nodes = {}
        for line in allocation.stdout.splitlines():
            match = re.fullmatch(r'NODE (binder|hwbinder|vndbinder) ([0-9]{1,10}) ([0-9]{1,10})', line)
            assert match and match[1] not in nodes
            nodes[match[1]] = {'major': int(match[2]), 'minor': int(match[3])}
        assert set(nodes) == {'binder', 'hwbinder', 'vndbinder'}
        for node, numbers in nodes.items():
            path = binderfs / node
            before = path.lstat()
            assert stat.S_ISCHR(before.st_mode)
            assert os.major(before.st_rdev) == numbers['major'] and os.minor(before.st_rdev) == numbers['minor']
            os.chmod(path, 0o666)
            after = path.lstat()
            assert (after.st_dev, after.st_ino, after.st_rdev) == (before.st_dev, before.st_ino, before.st_rdev)
            assert stat.S_ISCHR(after.st_mode) and stat.S_IMODE(after.st_mode) == 0o666
        report['owned_binder_character_devices'] = {node: True for node in nodes}
        report['phase'] = 'host_loop_mapper_devices'
        required_devices = ['/dev/loop-control', '/dev/mapper/control']
        until = min(deadline, time.monotonic() + 10)
        while time.monotonic() < until:
            if all(Path(path).exists() and stat.S_ISCHR(Path(path).stat().st_mode) for path in required_devices):
                break
            time.sleep(0.5)
        report['host_control_character_devices'] = {
            path: Path(path).exists() and stat.S_ISCHR(Path(path).stat().st_mode) for path in required_devices}
        assert all(report['host_control_character_devices'].values())
        report['distro_binder_loop_device_mapper_ready'] = True
        report['phase'] = 'image_pull'
        checked(docker + ['pull', '--quiet', '--platform=linux/arm64', image], 180)
        identity = json.loads(checked(docker + ['image', 'inspect', image]))[0]
        assert identity['Id'] == expected_image_id and identity['Architecture'] == 'arm64'
        assert identity['Os'] == 'linux' and image in identity['RepoDigests']
        report['pinned_image_id'] = identity['Id']
        if lab_args.stage in ('native', 'tls'):
            selected_profile = boot_profile('stock', 'bridge', [], 90)
        else:
            stock = boot_profile('stock', 'none', [], 90)
            stop_empty_stock(stock)
            report['phase'] = 'public_cache_overlay'
            cache_overlay = prepare_cache_overlay(docker, image, expected_image_id, work, owner, command)
            report['one_optional_cache_property_overlay_sha256'] = cache_overlay['sha256']
            overlays = hal['readonly_bind_targets'] + [cache_overlay]
            selected_profile = boot_profile('stock_warm_lavapipe', 'bridge', overlays, 180)
        report['graphics_profile'] = selected_profile['graphics_profile']
        report['boot_only_pass'] = selected_profile['boot_only_pass']
        report['phase'] = 'native_components' if lab_args.stage == 'native' else 'installed_runtime_test'
        bootenv = boot_environment(docker, cid, command)
        report['public_boot_environment_keys'] = sorted(bootenv)
        report['install_progress'] = {}
        selected = installed_component_stage(docker, cid, command, bootenv, hal, lab_args, deadline,
                                             report['install_progress'])
        if lab_args.stage == 'native':
            report['native_components'] = selected
            report['native_component9_pass'] = selected['passed']
        else:
            report['installed_runtime_test'] = selected
            report['installed_runtime_test_pass'] = selected['passed']
            if lab_args.stage == 'game':
                report.update({'verdict_scope': 'automated_game_capture_only',
                               'visual_login_review_required': True, 'visible_login_confirmed': False,
                               'dummy_input_test_completed': False, 'credential_use_approved': False})
        if not selected['passed']:
            raise RuntimeError('Actual selected component stage failed')
        report['unimplemented_stages'] = ['unsent_input']
        report['phase'] = 'complete'
    except Exception as failure:
        report['incomplete'] = True
        report['error_class'] = type(failure).__name__ if type(failure).__name__ in (
            'AssertionError', 'TimeoutError', 'TimeoutExpired', 'RuntimeError',
            'FileNotFoundError', 'PermissionError', 'ValueError', 'KeyError', 'OSError') else 'OtherError'
    finally:
        try:
            if cid is None and cidfile is not None and cidfile.is_file():
                candidate = cidfile.read_text().strip()
                if re.fullmatch('[0-9a-f]{64}', candidate):
                    cid = candidate
            if cid is not None:
                owned_container(cid, cleanup=True)
                subprocess.run(docker + ['stop', '--time', '10', cid], capture_output=True, timeout=20)
                removed = subprocess.run(docker + ['rm', '--force', cid], capture_output=True, timeout=20)
                report['owned_container_removed'] = removed.returncode == 0
        except Exception:
            report['owned_cleanup_incomplete'] = True
        try:
            if binder_mounted and (cid is None or report['owned_container_removed']):
                assert binder_mount_id is not None and owned_binder_mount() == binder_mount_id
                unmounted = subprocess.run(['umount', str(binderfs)], capture_output=True, timeout=20)
                report['owned_binderfs_unmounted'] = unmounted.returncode == 0 and owned_binder_mount() is None
        except Exception:
            report['owned_binderfs_cleanup_incomplete'] = True
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['ephemeral_stock_data_retained_on_disposable_runner'] = True
        report['passed'] = (report['phase'] == 'complete' and not report.get('incomplete', False)
                            and not report.get('error_class') and report['boot_only_pass']
                            and (report.get('native_component9_pass', False) if lab_args.stage == 'native'
                                 else report.get('installed_runtime_test_pass', False))
                            and report['owned_container_removed'] and report['owned_binderfs_unmounted'])
        print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == "__main__":
    raise SystemExit(main())
