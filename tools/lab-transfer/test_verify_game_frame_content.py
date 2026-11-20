"""Pure metadata/pixel/string checks; never load Pillow, Tesseract, or game files."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import unittest


spec = importlib.util.spec_from_file_location('verify_game_frame_content',
    Path(__file__).with_name('verify_game_frame_content.py'))
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def receipt():
    return {'scope': subject.SCOPE, 'account_input_sent': False, 'synthetic_drawables': False,
        'frames': [{'file': f'handheld-game-123-{index}.png', 'width': 960, 'height': 540,
            'sha256': '0' * 64,
            'windows': [{'id': 1, 'class': 'Wow-ARM64.exe', 'width': 960, 'height': 540}],
            'native_processes': [{'android_pid': 123, 'selected_game_argv0': True, 'same_uid': True,
                'installed_wine_mapping_identity': ['libwow_wine_ntdll.so', 'libwow_wine_loader.so']}]}
            for index in range(3)]}


class FrameReceiptTests(unittest.TestCase):
    def test_exact_current_qa_receipt_has_three_bound_game_frames(self):
        self.assertEqual(len(subject.validate_receipt(receipt())), 3)

    def test_account_input_or_synthetic_frames_rejected(self):
        for key in ('account_input_sent', 'synthetic_drawables'):
            value = receipt()
            value[key] = True
            with self.subTest(key=key), self.assertRaises(subject.ContentError):
                subject.validate_receipt(value)

    def test_traversal_duplicate_or_wrong_frame_slot_rejected(self):
        for name in ('../private.png', 'handheld-game-123-1.png', '/tmp/handheld-game-123-0.png'):
            value = receipt()
            value['frames'][0]['file'] = name
            with self.subTest(name=name), self.assertRaises(subject.ContentError):
                subject.validate_receipt(value)

    def test_missing_actual_window_process_or_wine_identity_rejected(self):
        for kind in ('windows', 'native_processes', 'wine_identity', 'same_uid', 'argv0'):
            value = receipt()
            if kind in ('windows', 'native_processes'):
                value['frames'][0][kind] = []
            elif kind == 'wine_identity':
                value['frames'][0]['native_processes'][0]['installed_wine_mapping_identity'] = ['libwow_wine_loader.so']
            else:
                key = 'same_uid' if kind == 'same_uid' else 'selected_game_argv0'
                value['frames'][0]['native_processes'][0][key] = False
            with self.subTest(kind=kind), self.assertRaises(subject.ContentError):
                subject.validate_receipt(value)

    def test_huge_dimensions_or_wrong_receipt_scope_rejected(self):
        value = receipt()
        value['frames'][0]['width'] = subject.MAX_PIXELS
        with self.assertRaises(subject.ContentError):
            subject.validate_receipt(value)
        value = receipt()
        value['scope'] = 'UNCLASSIFIED startup only'
        with self.assertRaises(subject.ContentError):
            subject.validate_receipt(value)

    def test_duplicate_json_keys_rejected_without_echoing_content(self):
        with self.assertRaises(subject.ContentError) as failure:
            json.loads('{"private-canary":1,"private-canary":2}', object_pairs_hook=subject.unique_object)
        self.assertNotIn('private-canary', str(failure.exception))

    def test_png_digest_and_header_dimensions_bound_without_pillow(self):
        raw = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 960, 540)
        frame = {'sha256': hashlib.sha256(raw).hexdigest(), 'width': 960, 'height': 540}
        subject.validate_png_bytes(raw, frame)
        with self.assertRaises(subject.ContentError):
            subject.validate_png_bytes(raw + b'changed', frame)
        frame = {**frame, 'width': 1280}
        with self.assertRaises(subject.ContentError):
            subject.validate_png_bytes(raw, frame)


class FixedContentObservationTests(unittest.TestCase):
    def test_login_labels_case_spacing_and_punctuation_only(self):
        for text in ('Email or Phone\nPassword\nLog In', 'EMAIL-OR-PHONE pass word LOGIN', 'EmailorPhone PASSWORD Log In'):
            with self.subTest(text=text):
                self.assertTrue(all(subject.observed_labels(text).values()))

    def test_unknown_ocr_text_never_survives_output(self):
        private = 'canary@example.invalid secret-path-password123'
        result = subject.observed_labels(private)
        self.assertEqual(set(result), set(subject.LABELS))
        self.assertTrue(all(type(value) is bool for value in result.values()))
        self.assertNotIn(private, json.dumps(result))

    def test_exact_nonblack_colored_and_texture_thresholds(self):
        result = subject.rgb_metrics(bytes((0, 0, 0, 31, 0, 0, 32, 32, 32, 255, 220, 0)), 4, 1)
        self.assertEqual(result['sample_pixels'], 4)
        self.assertEqual(result['nonblack_pixels'], 2)
        self.assertEqual(result['colored_pixels'], 1)
        self.assertEqual(result['horizontal_neighbor_pairs'], 3)
        self.assertEqual(result['edge_pairs_delta_ge_16'], 3)
        self.assertEqual(result['distinct_rgb444_bins'], 4)

    def test_motion_threshold_and_incomparable_dimensions_are_explicit(self):
        before = bytes((0, 0, 0, 32, 32, 32))
        after = bytes((15, 15, 15, 48, 32, 32))
        result = subject.rgb_motion(before, after)
        self.assertTrue(result['comparable'])
        self.assertEqual(result['changed_pixels_delta_ge_16'], 1)
        self.assertFalse(subject.rgb_motion(before, after[:3])['comparable'])

    def test_invalid_caller_data_cannot_approve_any_gate(self):
        result = subject.verify_game_frame_content('not-an-owned-path', 'private-canary',
            data_identity=(1, 2), owner_marker=Path('absent'), owner='0' * 32, deadline=10**12)
        self.assertFalse(result['analysis_complete'])
        for key in ('human_visual_review_completed', 's25_hardware_test_completed',
                    'dummy_input_test_completed', 'credential_use_approved', 'raw_ocr_or_images_exported'):
            self.assertFalse(result[key])
        self.assertNotIn('private-canary', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
