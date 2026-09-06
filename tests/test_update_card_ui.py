import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok.ui.qt.about.UpdateCard import UpdateCard
from PySide6.QtWidgets import QApplication

from src.compat.update_card_ui import (
    MIN_CHECK_UPDATES_LAUNCHER_VERSION,
    PATCH_MARKER,
    UPDATE_CARD_STATUS_WRAP_WIDTH,
    install_update_card_ui,
    launcher_supports_update_check,
    parse_launcher_version,
    status_width_for_text,
)

UNSUPPORTED_MESSAGE = "Update checking is not supported by this PyAppify version."
DOWNLOAD_URL = "https://github.com/GodRaymond233/ok-bd2/releases/latest"


def make_pyappify_module(pyappify_version=None):
    module = types.SimpleNamespace()
    module.pyappify_version = pyappify_version
    return module


class UpdateCardUiCompatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_update_card_ui()

    def make_card(self, pyappify_version=None):
        return UpdateCard(
            "v1.2.0",
            make_pyappify_module(pyappify_version),
            download_url=DOWNLOAD_URL,
        )

    def test_parse_launcher_version(self):
        self.assertEqual((1, 1, 9), parse_launcher_version("1.1.9"))
        self.assertEqual((1, 2, 3), parse_launcher_version("v1.2.3"))
        self.assertIsNone(parse_launcher_version("V1.2.3"))
        self.assertIsNone(parse_launcher_version(None))
        self.assertIsNone(parse_launcher_version("dev"))
        self.assertIsNone(parse_launcher_version(""))

    def test_launcher_supports_update_check_threshold(self):
        self.assertIsNone(launcher_supports_update_check(None))
        self.assertIsNone(launcher_supports_update_check("dev"))
        self.assertFalse(launcher_supports_update_check("1.1.9"))
        self.assertTrue(
            launcher_supports_update_check(
                ".".join(str(part) for part in MIN_CHECK_UPDATES_LAUNCHER_VERSION)
            )
        )
        self.assertTrue(launcher_supports_update_check("1.2.3"))

    def test_install_marks_patch_and_fits_initial_status(self):
        card = self.make_card("1.2.3")
        self.assertTrue(getattr(UpdateCard, PATCH_MARKER, False))
        expected = status_width_for_text(card.status_label, card.status_label.text())
        self.assertGreater(expected, 0)
        self.assertEqual(expected, card.status_label.minimumWidth())
        self.assertEqual(expected, card.status_label.maximumWidth())

    def test_short_status_pins_natural_single_line_width(self):
        card = self.make_card("1.2.3")
        card.show()
        card._set_status("没有可用更新。")
        expected = status_width_for_text(card.status_label, "没有可用更新。")
        self.assertLess(expected, UPDATE_CARD_STATUS_WRAP_WIDTH)
        self.assertEqual(expected, card.status_label.minimumWidth())
        self.assertEqual(expected, card.status_label.maximumWidth())

    def test_long_status_wraps_at_cap_and_recovers(self):
        card = self.make_card("1.2.3")
        card.show()
        long_message = "检查更新失败: something went wrong with a very long english detail"
        card._set_status(long_message, error=True)
        self.assertEqual(UPDATE_CARD_STATUS_WRAP_WIDTH, card.status_label.minimumWidth())
        self.assertEqual(UPDATE_CARD_STATUS_WRAP_WIDTH, card.status_label.maximumWidth())
        card._set_status("没有可用更新。")
        self.assertLess(
            card.status_label.minimumWidth(), UPDATE_CARD_STATUS_WRAP_WIDTH
        )

    def test_empty_status_collapses(self):
        card = self.make_card("1.2.3")
        card.show()
        card._set_status("")
        self.assertEqual(0, card.status_label.maximumWidth())
        self.assertFalse(card.status_label.isVisible())

    def test_controls_wrap_to_second_row_when_narrow(self):
        card = self.make_card("1.2.3")
        card.show()
        card.resize(360, 200)
        for _ in range(4):
            QApplication.processEvents()
        self.assertGreater(
            card.update_button.y(),
            card.status_label.y(),
            "窄宽度下控件应折到下一行而不是被右缘裁掉",
        )

    def test_check_for_updates_intercepts_old_launcher_with_guidance(self):
        card = self.make_card("1.1.9")
        card.show()
        card.check_for_updates()
        self.assertIn("启动器版本 1.1.9 过旧", card.status_label.text())
        self.assertIn("Launcher 1.1.9 is too old", card.status_label.text())
        self.assertEqual(
            UPDATE_CARD_STATUS_WRAP_WIDTH, card.status_label.minimumWidth()
        )
        self.assertTrue(card.download_button.isVisible())

    def test_check_for_updates_without_launcher_version_uses_upstream_path(self):
        card = self.make_card(None)
        card.show()
        card.check_for_updates()
        self.assertEqual(UNSUPPORTED_MESSAGE, card.status_label.text())

    def test_check_for_updates_new_launcher_uses_upstream_path(self):
        card = self.make_card("1.2.3")
        card.show()
        card.check_for_updates()
        self.assertEqual(UNSUPPORTED_MESSAGE, card.status_label.text())


if __name__ == "__main__":
    unittest.main()
