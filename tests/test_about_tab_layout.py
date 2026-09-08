import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok.ui.qt.about.LinksBar import LinksBar
from ok.ui.qt.about.ProjectCard import ProjectCard
from ok.ui.qt.about.VersionCard import VersionCard
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon

from src.compat.about_tab_layout import _flow_links_bar, _shrink_setting_card_labels
from src.ui.wrap_layout import wrap_container


class AboutTabLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_share_button_copies_after_layout_replacement(self):
        card = VersionCard(
            {"links": {"default": {"share": "https://example.com/share"}}},
            FluentIcon.INFO, "ok-bd2", "v1.2.4", False,
        )
        self.addCleanup(card.deleteLater)
        button = card.findChild(LinksBar).share_button
        _flow_links_bar(card)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        clipboard = self.app.clipboard()
        previous = clipboard.text()
        self.addCleanup(clipboard.setText, previous)
        clipboard.setText("unchanged")
        with patch("ok.ui.qt.about.LinksBar.alert_info"):
            button.click()
        self.assertEqual("https://example.com/share", clipboard.text())

    def test_project_buttons_stay_inside_narrow_container(self):
        card = ProjectCard(
            "ok-script App Template", "https://github.com/ok-oldking/ok-script-app",
            "https://app.ok-script.com/",
        )
        _shrink_setting_card_labels(card, "titleLabel", "contentLabel")
        host = QWidget()
        self.addCleanup(host.deleteLater)
        box = QVBoxLayout(host)
        container, _wrap = wrap_container([card])
        box.addWidget(container)
        host.show()
        for width in (420, 900, 420):
            host.resize(width, 200)
            for _ in range(4):
                self.app.processEvents()
            self.assertLessEqual(card.geometry().right(), container.rect().right())
            for button in (card.github_button, card.download_button):
                right = button.mapTo(container, button.rect().bottomRight()).x()
                self.assertLessEqual(right, container.rect().right())


if __name__ == "__main__":
    unittest.main()
