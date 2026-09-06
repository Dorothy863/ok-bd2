import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.ui.wrap_layout import wrap_container


class WrapLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_host(self, count=4):
        host = QWidget()
        box = QVBoxLayout(host)
        buttons = [QPushButton(f"按钮{i}") for i in range(count)]
        container, wrap = wrap_container(buttons)
        box.addWidget(container)
        return host, container, wrap, buttons

    def test_wraps_when_narrow_and_unwraps_when_wide(self):
        host, container, wrap, buttons = self.make_host()
        host.resize(140, 400)
        host.show()
        QApplication.processEvents()
        self.assertGreater(
            buttons[1].y(), buttons[0].y(), "宽度不足时第二个按钮应折到下一行"
        )

        host.resize(900, 400)
        QApplication.processEvents()
        self.assertEqual(buttons[1].y(), buttons[0].y(), "宽度足够时应回到同一行")

    def test_minimum_width_is_widest_child_not_row_sum(self):
        host, container, wrap, buttons = self.make_host()
        host.show()
        QApplication.processEvents()
        widest = max(b.minimumSizeHint().width() for b in buttons)
        row_sum = sum(b.sizeHint().width() for b in buttons)
        min_width = wrap.minimumSize().width()
        self.assertGreaterEqual(min_width, widest)
        self.assertLess(min_width, row_sum, "最小宽度不得等于整行求和（会把宿主页锁宽）")

    def test_container_declares_height_for_width(self):
        host, container, wrap, buttons = self.make_host()
        self.assertTrue(container.sizePolicy().hasHeightForWidth())
        self.assertTrue(wrap.hasHeightForWidth())
        narrow = wrap.heightForWidth(140)
        wide = wrap.heightForWidth(900)
        self.assertGreater(narrow, wide)

    def test_hidden_widgets_are_skipped(self):
        host, container, wrap, buttons = self.make_host()
        buttons[1].hide()
        host.resize(900, 400)
        host.show()
        QApplication.processEvents()
        self.assertEqual(buttons[2].y(), buttons[0].y())
        self.assertLess(buttons[2].x(), 900)


if __name__ == "__main__":
    unittest.main()
