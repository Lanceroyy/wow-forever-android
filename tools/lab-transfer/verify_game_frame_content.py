"""Inspect only the lab's three QA game frames; export fixed observations only."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time
import unicodedata


PACKAGE = 'app.wowforever.handheld'
SCOPE = 'Actual Session render-path captures; visual review required; no login assertion'
WINE_NAMES = {'libwow_wine_ntdll.so', 'libwow_wine_loader.so', 'libwow_wine_preloader.so'}
MAX_PIXELS = 4_194_304
LABELS = ('email_or_phone', 'password', 'log_in')
# Fixed proportions of the user's 960x540 reference. The horizontal bounds
# exclude its cursor; no search or frame registration moves these bands.
GOLD_CROP = (39, 43, 61, 81)
GOLD_LABEL_BANDS = (('email_or_phone', 44, 50), ('password', 55, 61), ('log_in', 74, 80))


class ContentError(Exception):
    pass


def require(value, code):
    if not value:
        raise ContentError(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_receipt_key')
        result[key] = value
    return result


def validate_receipt(value):
    """Validate the exact current QA schema without exporting its names or paths."""
    require(isinstance(value, dict) and set(value) == {'scope', 'account_input_sent', 'synthetic_drawables', 'frames'},
            'receipt_schema')
    require(value['scope'] == SCOPE and value['account_input_sent'] is False
            and value['synthetic_drawables'] is False, 'receipt_scope')
    frames = value['frames']
    require(isinstance(frames, list) and len(frames) == 3, 'receipt_frame_count')
    names = set()
    for index, frame in enumerate(frames):
        require(isinstance(frame, dict) and set(frame) ==
                {'file', 'width', 'height', 'sha256', 'windows', 'native_processes'}, 'frame_schema')
        name = frame['file']
        require(isinstance(name, str) and re.fullmatch(r'handheld-game-[0-9]{1,16}-' + str(index) + r'\.png', name)
                and name not in names, 'frame_name')
        names.add(name)
        width, height = frame['width'], frame['height']
        require(type(width) is int and type(height) is int and width > 0 and height > 0
                and width * height <= MAX_PIXELS, 'frame_dimensions')
        require(isinstance(frame['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', frame['sha256']), 'frame_digest')
        windows = frame['windows']
        processes = frame['native_processes']
        require(isinstance(windows, list) and 1 <= len(windows) <= 32, 'game_window_count')
        for window in windows:
            require(isinstance(window, dict) and set(window) == {'id', 'class', 'width', 'height'}, 'game_window_schema')
            require(type(window['id']) is int and window['id'] != 0 and -(2**31) <= window['id'] < 2**32,
                    'game_window_identity')
            require(isinstance(window['class'], str) and 1 <= len(window['class']) <= 1024
                    and 'wow' in window['class'].casefold(), 'game_window_class')
            require(all(type(window[key]) is int and 0 < window[key] <= 65535 for key in ('width', 'height')),
                    'game_window_dimensions')
        require(isinstance(processes, list) and 1 <= len(processes) <= 32, 'game_process_count')
        for process in processes:
            require(isinstance(process, dict) and set(process) ==
                    {'android_pid', 'selected_game_argv0', 'same_uid', 'installed_wine_mapping_identity'},
                    'game_process_schema')
            require(type(process['android_pid']) is int and 1 < process['android_pid'] < 2**31
                    and process['selected_game_argv0'] is True and process['same_uid'] is True,
                    'game_process_identity')
            mappings = process['installed_wine_mapping_identity']
            require(isinstance(mappings, list) and 2 <= len(mappings) <= 3
                    and all(isinstance(name, str) and name in WINE_NAMES for name in mappings)
                    and len(set(mappings)) == len(mappings) and 'libwow_wine_ntdll.so' in mappings
                    and bool(set(mappings) & {'libwow_wine_loader.so', 'libwow_wine_preloader.so'}),
                    'game_process_wine_identity')
    return frames


def observed_labels(text):
    """Only known literal UI labels survive; recognized text is never returned."""
    normalized = unicodedata.normalize('NFKC', text).casefold()
    normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
    return {
        'email_or_phone': bool(re.search(r'\bemail\s*(?:or\s*)?phone\b', normalized)),
        'password': bool(re.search(r'\bpass\s*word\b', normalized)),
        'log_in': bool(re.search(r'\blog\s*in\b', normalized)),
    }


def validate_png_bytes(raw, frame):
    require(hashlib.sha256(raw).hexdigest() == frame['sha256'], 'png_digest_mismatch')
    require(len(raw) >= 24 and raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[12:16] == b'IHDR'
            and struct.unpack('>II', raw[16:24]) == (frame['width'], frame['height']),
            'png_header_dimensions')


def rgb_metrics(raw, width, height):
    require(len(raw) == width * height * 3 and width > 0 and height > 0, 'pixel_buffer')
    nonblack = colored = edges = 0
    colors = set()
    previous = (0, 0, 0)
    for index, (red, green, blue) in enumerate(zip(raw[0::3], raw[1::3], raw[2::3])):
        high, low = max(red, green, blue), min(red, green, blue)
        nonblack += high >= 32
        colored += high >= 32 and high - low >= 16
        colors.add((red >> 4, green >> 4, blue >> 4))
        if index % width:
            edges += max(abs(red - previous[0]), abs(green - previous[1]), abs(blue - previous[2])) >= 16
        previous = red, green, blue
    return {'sample_pixels': width * height, 'nonblack_pixels': nonblack, 'colored_pixels': colored,
            'horizontal_neighbor_pairs': (width - 1) * height, 'edge_pairs_delta_ge_16': edges,
            'distinct_rgb444_bins': len(colors)}


def rgb_motion(before, after):
    require(len(before) % 3 == 0 and len(after) % 3 == 0, 'motion_buffer')
    if len(before) != len(after):
        return {'comparable': False, 'sample_pixels': None, 'changed_pixels_delta_ge_16': None}
    changed = sum(max(abs(before[at + channel] - after[at + channel]) for channel in range(3)) >= 16
                  for at in range(0, len(before), 3))
    return {'comparable': True, 'sample_pixels': len(before) // 3, 'changed_pixels_delta_ge_16': changed}


def gold_stroke_mask(rgb):
    """Keep only reference-colored gold strokes; the mask stays private."""
    require(len(rgb) > 0 and len(rgb) % 3 == 0, 'gold_pixel_buffer')
    return bytes(255 if red >= 140 and green >= 95 and red >= green - 12
                 and red - green <= 82 and blue * 2 <= green and green - blue >= 60 else 0
                 for red, green, blue in zip(rgb[0::3], rgb[1::3], rgb[2::3]))


def stationary_gold_labels(frames, deadline):
    """Observe fixed labels on an explicitly identified three-frame composite."""
    require(len(frames) == 3, 'gold_frame_count')
    require(time.monotonic() < deadline, 'analysis_deadline')
    result = {'analysis_complete': False, 'three_aligned_frames': False,
              'scope': 'Three-frame gold-mask intersection; fixed ordered label bands only',
              'input_gold_pixels': [raw.count(255) for _, _, raw in frames],
              'stationary_gold_pixels': None, 'band_gold_pixels': dict.fromkeys(LABELS, None),
              'login_labels': dict.fromkeys(LABELS, False), 'all_three_login_labels_observed': False}
    if len({(full_size, size) for full_size, size, _ in frames}) != 1:
        result['analysis_complete'] = True
        return result
    (width, height), size, _ = frames[0]
    left, top, right, bottom = GOLD_CROP
    require(size == (width * right // 100 - width * left // 100,
                     height * bottom // 100 - height * top // 100)
            and min(size) > 0 and all(len(raw) == size[0] * size[1] for _, _, raw in frames),
            'gold_mask_dimensions')
    from PIL import Image, ImageChops
    with ExitStack() as closing:
        masks = []
        for _, _, raw in frames:
            mask = Image.frombytes('L', size, raw)
            closing.callback(mask.close)
            masks.append(mask)
        first = ImageChops.darker(masks[0], masks[1])
        closing.callback(first.close)
        stationary = ImageChops.darker(first, masks[2])
        closing.callback(stationary.close)
        result['three_aligned_frames'] = True
        result['stationary_gold_pixels'] = stationary.tobytes().count(255)
        for label, upper, lower in GOLD_LABEL_BANDS:
            require(time.monotonic() < deadline, 'analysis_deadline')
            band = stationary.crop((0, height * upper // 100 - height * top // 100,
                                    size[0], height * lower // 100 - height * top // 100))
            try:
                count = band.tobytes().count(255)
                result['band_gold_pixels'][label] = count
                if not count:
                    continue
                inverted = band.point(lambda value: 255 - value)
                try:
                    enlarged = inverted.resize((band.width * 3, band.height * 3), Image.Resampling.BICUBIC)
                    try:
                        result['login_labels'][label] = ocr_labels(enlarged, 7, deadline)[label]
                    finally:
                        enlarged.close()
                finally:
                    inverted.close()
            finally:
                band.close()
        result['all_three_login_labels_observed'] = all(result['login_labels'].values())
        result['analysis_complete'] = True
    return result


def file_identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def read_regular(directory, name, maximum, device):
    descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_dev == device
                and 0 < before.st_size <= maximum, 'capture_file_type_or_size')
        blocks = []
        count = 0
        while True:
            block = os.read(descriptor, min(65536, maximum + 1 - count))
            if not block:
                break
            blocks.append(block)
            count += len(block)
            require(count <= maximum, 'capture_file_limit')
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require(count == before.st_size and file_identity(before) == file_identity(after) == file_identity(named),
                'capture_file_changed')
        return b''.join(blocks), file_identity(after)
    finally:
        os.close(descriptor)


def ocr_labels(image, psm, deadline):
    """Tesseract input/output stay in pipes/anonymous RAM, never named files."""
    import resource
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'analysis_deadline')
    source = io.BytesIO()
    image.save(source, format='PNG')
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'analysis_deadline')
    limit = 65536

    def bound_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    descriptor = os.memfd_create('wow-ocr-output', os.MFD_CLOEXEC)
    with os.fdopen(descriptor, 'w+b') as output:
        result = subprocess.run(['/usr/bin/tesseract', 'stdin', 'stdout', '-l', 'eng', '--psm', str(psm)],
            input=source.getvalue(), stdout=output, stderr=subprocess.DEVNULL,
            timeout=min(3 if psm == 7 else 6, remaining), preexec_fn=bound_output,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'OMP_THREAD_LIMIT': '1'})
        output.seek(0)
        text = output.read(limit + 1)
    require(result.returncode == 0 and len(text) < limit, 'ocr_incomplete')
    return observed_labels(text.decode('utf-8', errors='replace'))


def verify_game_frame_content(data_root, receipt_basename, *, data_identity, owner_marker, owner, deadline):
    result = {'analysis_complete': False, 'scope': 'Fixed game-frame content observations only',
              'human_visual_review_completed': False, 's25_hardware_test_completed': False,
              'dummy_input_test_completed': False, 'credential_use_approved': False,
              'raw_ocr_or_images_exported': False, 'frames': [], 'phase': 'owned_directory'}
    started = time.monotonic()
    deadline = min(deadline, started + 45)
    try:
        require(os.name == 'posix' and hasattr(os, 'memfd_create'), 'unsupported_analysis_host')
        data_root, owner_marker = Path(data_root), Path(owner_marker)
        require(data_root.is_absolute() and data_root.resolve() == data_root and data_root.name == 'data'
                and data_root.parent.name.startswith('wow-arm64-boot-')
                and owner_marker == data_root.parent / '.owned-arm64-boot', 'owned_data_path')
        require(isinstance(owner, str) and re.fullmatch(r'[0-9a-f]{32}', owner), 'owned_marker_value')
        require(isinstance(receipt_basename, str)
                and re.fullmatch(r'handheld-game-captures-[0-9]{1,16}\.json', receipt_basename), 'receipt_name')
        require(len(data_identity) == 2 and all(type(value) is int and value >= 0 for value in data_identity),
                'owned_data_identity')
        with ExitStack() as closing:
            marker_fd = os.open(owner_marker, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            closing.callback(os.close, marker_fd)
            marker_stat = os.fstat(marker_fd)
            require(stat.S_ISREG(marker_stat.st_mode) and marker_stat.st_nlink == 1 and marker_stat.st_size == 32
                    and os.read(marker_fd, 33) == owner.encode('ascii'), 'owned_marker_mismatch')
            root_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            closing.callback(os.close, root_fd)
            identity = os.fstat(root_fd)
            require((identity.st_dev, identity.st_ino) == tuple(data_identity), 'owned_data_mismatch')
            directory = root_fd
            for part in ('media', '0', 'Android', 'data', PACKAGE, 'files'):
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                                    dir_fd=directory)
                closing.callback(os.close, directory)
                require(os.fstat(directory).st_dev == identity.st_dev, 'capture_directory_device')
            result['phase'] = 'receipt_validation'
            receipt, receipt_identity = read_regular(directory, receipt_basename, 262144, identity.st_dev)
            frames = validate_receipt(json.loads(receipt, object_pairs_hook=unique_object))
            result['receipt_and_game_metadata_validated'] = True
            from PIL import Image
            bands = []
            gold_frames = []
            bound_identities = {receipt_basename: receipt_identity}
            for index, frame in enumerate(frames):
                result['phase'] = 'png_validation'
                require(time.monotonic() < deadline, 'analysis_deadline')
                raw, bound_identities[frame['file']] = read_regular(directory, frame['file'], 20 * 1024 * 1024, identity.st_dev)
                validate_png_bytes(raw, frame)
                with Image.open(io.BytesIO(raw)) as opened:
                    require(opened.format == 'PNG' and opened.size == (frame['width'], frame['height'])
                            and getattr(opened, 'n_frames', 1) == 1, 'png_image_format')
                    opened.load()
                    image = opened.convert('RGB')
                width, height = image.size
                left, top, right, bottom = GOLD_CROP
                with image.crop((width * left // 100, height * top // 100,
                                 width * right // 100, height * bottom // 100)) as gold:
                    gold_frames.append((image.size, gold.size, gold_stroke_mask(gold.tobytes())))
                row = {'index': index, 'width': width, 'height': height,
                       'game_window_count': len(frame['windows']), 'native_game_process_count': len(frame['native_processes']),
                       'png_digest_and_dimensions_verified': True, 'peripheral': {},
                       'ocr_complete': False, 'login_labels': {key: False for key in LABELS}}
                result['phase'] = 'pixel_analysis'
                frame_bands = {}
                for side, left, right in (('left', 8, 30), ('right', 70, 92)):
                    crop = image.crop((width * left // 100, height * 20 // 100, width * right // 100, height * 80 // 100))
                    pixels = crop.tobytes()
                    row['peripheral'][side] = rgb_metrics(pixels, *crop.size)
                    frame_bands[side] = (crop.size, pixels)
                    crop.close()
                bands.append(frame_bands)
                result['frames'].append(row)
                central = image.crop((width * 20 // 100, height * 20 // 100, width * 80 // 100, height * 90 // 100))
                if central.width * central.height <= MAX_PIXELS // 4:
                    enlarged = central.resize((central.width * 2, central.height * 2))
                    central.close()
                    central = enlarged
                try:
                    result['phase'] = 'ocr_full'
                    full = ocr_labels(image, 11, deadline)
                    result['phase'] = 'ocr_central'
                    middle = ocr_labels(central, 6, deadline)
                    row['login_labels'] = {key: full[key] or middle[key] for key in LABELS}
                    row['full_ocr_labels'] = full
                    row['central_ocr_labels'] = middle
                    row['ocr_complete'] = True
                finally:
                    central.close()
                    image.close()
            result['phase'] = 'stationary_gold_ocr'
            result['stationary_gold_label_check'] = stationary_gold_labels(gold_frames, deadline)
            result['phase'] = 'motion_analysis'
            result['peripheral_motion'] = []
            for first, second in ((0, 1), (1, 2)):
                entry = {'from_index': first, 'to_index': second}
                for side in ('left', 'right'):
                    before, after = bands[first][side], bands[second][side]
                    entry[side] = (rgb_motion(before[1], after[1]) if before[0] == after[0] else
                                   {'comparable': False, 'sample_pixels': None, 'changed_pixels_delta_ge_16': None})
                result['peripheral_motion'].append(entry)
            for name, expected in bound_identities.items():
                require(file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == expected,
                        'capture_changed_during_analysis')
            current_root = data_root.lstat()
            require(stat.S_ISDIR(current_root.st_mode)
                    and (current_root.st_dev, current_root.st_ino) == tuple(data_identity), 'data_changed_during_analysis')
            require(file_identity(os.fstat(marker_fd)) == file_identity(marker_stat)
                    == file_identity(owner_marker.lstat()), 'owned_marker_changed_during_analysis')
            require(time.monotonic() < deadline, 'analysis_deadline')
            result['known_login_labels_cooccurred'] = any(all(frame['login_labels'].values()) for frame in result['frames'])
            result['analysis_complete'] = True
            result['phase'] = 'complete'
    except ContentError as failure:
        result['failure_class'] = str(failure)
    except subprocess.TimeoutExpired:
        result['failure_class'] = 'ocr_timeout'
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        result['failure_class'] = 'analysis_input_or_dependency_unavailable'
    except Exception:
        result['failure_class'] = 'analysis_processing_error'
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result
