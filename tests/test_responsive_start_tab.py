import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok.ui.qt.widget.StatusBar import StatusBar
from ok.ui.qt.widget.Tab import Tab
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from qfluentwidgets import FluentIcon, PushButton, SettingCard

from src.ui.live_screenshot import install_start_tab_responsive
from src.ui.responsive_start_tab import (
    StartCardResponsiveController,
    install_responsive_start_tab,
)


class ResponsiveStartTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def settle(self):
        for _ in range(12):
            self.app.processEvents()

    def make_tab(self):
        tab = Tab()
        self.addCleanup(tab.deleteLater)
        card = SettingCard(FluentIcon.INFO, "ok-bd2", "v1.2.4")
        card.iconLabel.setFixedSize(30, 30)
        card.status_bar = StatusBar("test")
        card.hBoxLayout.addWidget(card.status_bar)
        card.status_bar.hide()
        for name, text, icon in (
            ("capture_button", "Capture", FluentIcon.ZOOM),
            ("refresh_button", "Refresh", FluentIcon.SYNC),
            ("start_button", "Start", FluentIcon.PLAY),
        ):
            button = PushButton(icon, text, card)
            setattr(card, name, button)
            card.hBoxLayout.addWidget(button)
        tab.start_card = card
        tab.add_widget(card)
        tab.debug_widget = QWidget()
        tab.debug_layout = QHBoxLayout(tab.debug_widget)
        for text in ("Report", "Logs", "OCR"):
            tab.debug_layout.addWidget(PushButton(text))
        tab.add_card("Debug", tab.debug_widget)
        tab.resize(800, 500)
        tab.show()
        self.settle()
        return tab

    def assert_controls_fit(self, tab):
        self.assertLessEqual(tab.view.width(), tab.viewport().width())
        card = tab.start_card
        for button in (card.capture_button, card.refresh_button, card.start_button):
            self.assertTrue(button.isVisible())
            self.assertLessEqual(
                button.mapTo(tab.view, button.rect().bottomRight()).x(),
                tab.viewport().width() - 1,
            )

    def test_status_changes_reflow_without_resizing_window(self):
        tab = self.make_tab()
        install_responsive_start_tab(tab)
        self.settle()
        controller = tab.start_card.findChild(StartCardResponsiveController)
        viewport_width = tab.viewport().width()
        self.assertEqual("single", controller.current_mode)
        status = tab.start_card.status_bar
        status.setTitle("Paused: PC Game Window Must Be in Front!")
        status.show()
        self.settle()
        self.assertEqual(viewport_width, tab.viewport().width())
        self.assertEqual("double", controller.current_mode)
        self.assert_controls_fit(tab)
        status.setTitle("OK")
        self.settle()
        self.assertEqual("single", controller.current_mode)
        self.assert_controls_fit(tab)
        status.setTitle("Running: " + "Long task name " * 10)
        self.settle()
        self.assertEqual("double", controller.current_mode)
        self.assert_controls_fit(tab)
        status.hide()
        self.settle()
        self.assertEqual("single", controller.current_mode)
        self.assert_controls_fit(tab)

    def test_existing_debug_wrap_survives_install_and_width_changes(self):
        tab = self.make_tab()
        install_start_tab_responsive(tab)
        debug_layout = tab.debug_layout
        buttons = [debug_layout.itemAt(i).widget() for i in range(debug_layout.count())]
        self.assertTrue(install_responsive_start_tab(tab))
        self.assertFalse(install_responsive_start_tab(tab))
        self.assertIs(debug_layout, tab.debug_layout)
        for width in (600, 1100, 600):
            tab.resize(width, 500)
            self.settle()
            self.assert_controls_fit(tab)
            self.assertEqual(buttons, [
                debug_layout.itemAt(i).widget() for i in range(debug_layout.count())
            ])
            for label in (tab.start_card.titleLabel, tab.start_card.contentLabel):
                self.assertTrue(label.isVisible())
                self.assertGreater(label.width(), 0)


if __name__ == "__main__":
    unittest.main()
