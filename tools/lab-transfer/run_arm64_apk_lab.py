#!/usr/bin/env python3
"""Owned native ARM64 Android component lab. Preparation only until reviewed.

Consumes three already downloaded local pinned inputs. It never downloads private
artifacts or receives an owner token. The first implemented stage is native9;
D3D, TLS and game stages remain explicitly unavailable until integrated/reviewed.
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


def installed_component_stage(docker, cid, command, bootenv, hal, args, deadline):
    """Actual fixed input installs and platform loader proof before native9."""
    def checked_android(values, seconds=20):
        value = command(docker + ['exec', cid] + values, seconds)
        if value.returncode:
            raise ValueError('Owned Android prerequisite failed')
        return value.stdout.strip()
    expected_namespace = 'bc09359d857bcd4a9028ee0bb2e0bfd99f8bb74de2c1c4ffe67fa00aff2620b1'
    namespace = checked_android(['/system/bin/sha256sum', '/linkerconfig/ld.config.txt'])
    if namespace.split()[0] != expected_namespace:
        raise ValueError('Public Android namespace differs from reviewed closure')
    if checked_android(['/system/bin/getprop', 'ro.hardware.vulkan']) != 'pastel':
        raise ValueError('Selected system Vulkan HAL differs')
    if checked_android(['/system/bin/getprop', 'service.sf.prime_shader_cache']) != 'false':
        raise ValueError('Reviewed optional shader-cache setting differs')
    if checked_android(['/system/bin/sha256sum', '/system/build.prop']).split()[0] != (
            'f76133a2f65993637c86b1694bbc646aa5fc337238bda6083ac28750d535d4f2'):
        raise ValueError('Public one-property boot overlay differs')
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
    prefix = docker + ['exec']
    for key, value in bootenv.items():
        prefix += ['--env', key + '=' + value]
    prefix += [cid]
    installed = {}
    for package, source, expected in ((PACKAGE, args.main, MAIN_SHA), (PACKAGE + '.test', args.qa, QA_SHA)):
        remote = '/data/local/tmp/' + ('main.apk' if package == PACKAGE else 'qa.apk')
        copied = command(docker + ['cp', str(source.resolve()), cid + ':' + remote], 60)
        if copied.returncode:
            raise ValueError('Owned APK copy failed')
        result = command(prefix + ['/system/bin/pm', 'install', '-r', '-t', remote], 120)
        if result.returncode or result.stdout.strip() != 'Success':
            raise ValueError('Package Manager did not confirm exact APK install')
        query = command(prefix + ['/system/bin/pm', 'path', package], 20)
        lines = query.stdout.strip().splitlines()
        if query.returncode or len(lines) != 1 or not re.fullmatch(r'package:/data/app/[A-Za-z0-9_/=+~.-]+/base\.apk', lines[0]):
            raise ValueError('Installed package base path is not unique')
        base = lines[0][len('package:'):]
        if checked_android(['/system/bin/sha256sum', base]).split()[0] != expected:
            raise ValueError('Installed base APK bytes differ')
        installed[package] = expected
    result = component_tests(docker, cid, command, bootenv, deadline)
    result['installed_apk_sha256'] = installed
    result['public_namespace_sha256'] = expected_namespace
    result['nine_public_hal_files_hash_checked_and_mapped'] = True
    result['native_bridge_override_or_env_mirroring_used'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', required=True, type=Path)
    parser.add_argument('--qa', required=True, type=Path)
    parser.add_argument('--hal', required=True, type=Path)
    parser.add_argument('--stage', choices=('native', 'd3d', 'tls', 'game', 'all'), default='native')
    args = parser.parse_args()
    if args.stage != 'native':
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
    name = 'wow-arm64-boot-' + owner[:16]
    work = Path(tempfile.mkdtemp(prefix='wow-arm64-boot-', dir='/tmp')).resolve()
    (work / '.owned-arm64-boot').write_text(owner)
    data = work / 'data'
    data.mkdir(mode=0o700)
    cidfile = work / 'container.cid'
    binderfs = work / 'binderfs'
    binderfs.mkdir(mode=0o700)
    binder_mount_id = None
    binder_mounted = False
    empty_config = '/proc/self/arm64-preflight-no-client-config'
    assert not Path(empty_config).exists()
    docker = [shutil.which('docker'), '--config', empty_config, '--host', 'unix:///var/run/docker.sock']
    started = time.monotonic()
    deadline = started + 1500
    cid = None
    report = {'scope': 'Native ARM64 JNI and protected generated GL only; not a game/login/credential verdict',
              'boot_only_pass': False, 'passed': False, 'hal_overlays': True, 'account_input_sent': False,
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

    try:
        assert os.geteuid() == 0 and platform.machine() == 'aarch64'
        assert os.sysconf('SC_PAGE_SIZE') == 4096
        assert docker[0] is not None
        hal = sibling('receive_public_hal').receive(lab_args.hal, work / 'hal')
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
        report['phase'] = 'public_cache_overlay'
        cache_overlay = prepare_cache_overlay(docker, image, expected_image_id, work, owner, command)
        report['one_optional_cache_property_overlay_sha256'] = cache_overlay['sha256']
        report['phase'] = 'container_create'
        args = docker + ['create', '--pull=never', '--platform=linux/arm64', '--name', name,
            '--cidfile', str(cidfile), '--label', 'io.wowforever.boot-owner=' + owner,
            '--privileged', '--network=bridge', '--cpus=4', '--memory=8g', '--memory-swap=8g',
            '--mount', 'type=bind,src=' + str(data) + ',dst=/data']
        for node in ('binder', 'hwbinder', 'vndbinder'):
            args += ['--mount', 'type=bind,src=' + str(binderfs / node) + ',dst=/dev/' + node]
        for entry in hal['readonly_bind_targets']:
            args += ['--mount', 'type=bind,src=' + entry['source'] + ',dst=' + entry['destination'] + ',readonly']
        args += ['--mount', 'type=bind,src=' + cache_overlay['source'] + ',dst=/system/build.prop,readonly']
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
        assert meta['HostConfig']['NetworkMode'] == 'bridge'
        assert not meta['HostConfig']['PortBindings']
        mounts = {row['Destination']: row for row in meta['Mounts']}
        for entry in hal['readonly_bind_targets']:
            row = mounts[entry['destination']]
            assert row['Type'] == 'bind' and row['RW'] is False and row['Source'] == entry['source']
        row = mounts['/system/build.prop']
        assert row['Type'] == 'bind' and row['RW'] is False and row['Source'] == cache_overlay['source']
        report['phase'] = 'container_start'
        checked(docker + ['start', cid], 30)
        report['owned_container_started'] = True
        report['phase'] = 'boot_wait'
        boot_deadline = min(deadline - 30, time.monotonic() + 300)
        boot = ''
        while time.monotonic() < boot_deadline:
            meta = owned_container(cid)
            if not meta['State']['Running']:
                report['container_exit_code'] = meta['State']['ExitCode']
                break
            try:
                query = command(docker + ['exec', cid, '/system/bin/getprop', 'sys.boot_completed'], 10)
                if query.returncode == 0 and query.stdout.strip() == '1':
                    boot = '1'
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(3)
        report['boot_completed'] = boot == '1'
        report['phase'] = 'readiness'
        properties = {}
        for key in ('ro.build.version.sdk', 'ro.product.cpu.abi', 'ro.dalvik.vm.native.bridge'):
            query = command(docker + ['exec', cid, '/system/bin/getprop', key], 10)
            value = query.stdout.strip() if query.returncode == 0 else ''
            properties[key] = value if re.fullmatch('[A-Za-z0-9._-]{0,64}', value) else 'unclassified'
        report['public_properties'] = properties
        services = {}
        for service in ('window', 'SurfaceFlinger'):
            query = command(docker + ['exec', cid, '/system/bin/service', 'check', service], 10)
            services[service] = query.returncode == 0 and query.stdout.strip() == 'Service ' + service + ': found'
        report['public_services_found'] = services
        readiness_deadline = min(deadline - 30, time.monotonic() + 90)
        report['readiness_samples'] = []
        report['wms_responsive'] = report['display_enabled'] = report['system_booted'] = report['display_ready'] = False

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
            report['wms_query_seconds'] = round(time.monotonic() - before_wms, 3)
            report['wms_query_exit'] = query.returncode
            report['wms_responsive'] = query.returncode == 0 and 'WINDOW MANAGER' in query.stdout
            report['display_enabled'] = 'mDisplayEnabled=true' in query.stdout
            report['system_booted'] = 'mSystemBooted=true' in query.stdout
            private_display = public_boolean(query.stdout, 'mDisplayEnabled')
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
            report['default_display_on'] = default_on
            report['private_display_enabled_observation'] = private_display
            report['display_ready'] = private_display == 'true' or (
                private_display == 'unavailable' and report['default_display_on'])
            report['display_readiness_source'] = ('verbose_wms' if private_display == 'true' else
                ('public_default_display_on' if report['display_ready'] else 'unavailable_or_not_ready'))
            sample = {'elapsed_seconds': round(time.monotonic() - started, 3),
                      'wms_exit': query.returncode, 'wms_responsive': report['wms_responsive'],
                      'wms_seconds': report['wms_query_seconds'], 'display_query_exit': display_exit,
                      'default_display_id': 0 if len(rows) == 1 else None,
                      'default_display_state': state, 'default_display_valid': valid,
                      'default_display_row_count': len(rows) if display_exit is not None else None,
                      'fixed_wms_flags': {
                          key: public_boolean(query.stdout, key) for key in (
                              'mDisplayEnabled', 'mSystemBooted', 'mBootAnimationStopped',
                              'mDisplayFrozen', 'mDisplayReady')}}
            report['readiness_samples'].append(sample)
            if report['wms_responsive'] and report['display_ready'] and report['system_booted']:
                sample['optional_diagnostics_skipped_after_readiness'] = True
                break
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
        report['container_running'] = meta['State']['Running']
        report['container_oom_killed'] = meta['State']['OOMKilled']
        report['boot_only_pass'] = (meta['State']['Running'] and not meta['State']['OOMKilled'] and boot == '1' and properties == {
            'ro.build.version.sdk': '35', 'ro.product.cpu.abi': 'arm64-v8a',
            'ro.dalvik.vm.native.bridge': '0'} and all(services.values()) and
            report['wms_responsive'] and report['display_ready'] and report['system_booted'])
        if not report['boot_only_pass']:
            raise RuntimeError('Actual platform readiness failed')
        report['phase'] = 'native_components'
        bootenv = boot_environment(docker, cid, command)
        report['public_boot_environment_keys'] = sorted(bootenv)
        report['native_components'] = installed_component_stage(docker, cid, command, bootenv, hal, lab_args, deadline)
        report['native_component9_pass'] = report['native_components']['passed']
        if not report['native_component9_pass']:
            raise RuntimeError('Actual native component stage failed')
        report['unimplemented_stages'] = ['d3d', 'tls', 'game', 'unsent_input']
        report['phase'] = 'complete'
    except Exception as failure:
        report['incomplete'] = True
        report['error_class'] = type(failure).__name__ if type(failure).__name__ in (
            'AssertionError', 'TimeoutError', 'TimeoutExpired', 'RuntimeError',
            'FileNotFoundError', 'PermissionError', 'ValueError', 'KeyError', 'OSError') else 'OtherError'
    finally:
        try:
            if cid is None and cidfile.is_file():
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
                            and report.get('native_component9_pass', False)
                            and report['owned_container_removed'] and report['owned_binderfs_unmounted'])
        print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == "__main__":
    raise SystemExit(main())
