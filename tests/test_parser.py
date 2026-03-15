import unittest

from micsync.parser import parse_recording_name


class ParserTest(unittest.TestCase):
    def test_orig_and_edit_share_group_key(self) -> None:
        orig = parse_recording_name("TX02_MIC001_20260608_112048_orig.wav")
        edit = parse_recording_name("TX02_MIC001_20260608_112048_edit.wav")
        self.assertEqual(orig.recording_group_key, "20260608_112048_TX02_MIC001")
        self.assertEqual(orig.recording_group_key, edit.recording_group_key)
        self.assertEqual(orig.variant, "orig")
        self.assertEqual(edit.variant, "edit")

    def test_suffixless_name_is_supported(self) -> None:
        parsed = parse_recording_name("TX00_MIC014_20260608_112048.wav")
        self.assertIsNone(parsed.variant)
        self.assertEqual(parsed.dest_name, "20260608_112048_TX00_MIC014.wav")
