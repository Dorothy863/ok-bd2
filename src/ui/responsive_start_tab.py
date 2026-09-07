"""启动页（StartTab）窄宽度响应式重排与自适应优化。

1. StartCard：
   - 宽屏（视口足够容纳单行）时采用标准紧凑单行模式（70px 高），
     图标、标题/版本、状态条、三个操作按钮均在同一水平线上，完全消除高度错位与空白。
   - 窄屏（视口不足以容纳单行）时自适应平滑折行为紧凑双行模式，
     第一行放图标、标题与状态条，第二行右对齐容纳截图/刷新/启动按钮。
   - 状态条随视口收窄自动进行省略处理并附加完整提示 Tooltip。

2. 实时截图与控制栏（实时截图行）：
   - 实时截图框随窗口横向拉伸按 16:9 比例放大，直到高度顶格（结合当前视口高度动态求取上限）。
   - 截图框达到顶格高度上限后固定宽度与高度，剩余水平拉伸空间完全由右侧
     “开发工具”与“手动调整分辨率”两栏向右充分铺开。
   - 视口过窄（如窄于 560px）时截图行自适应平滑堆叠为上下排列。

3. 选择窗口/截图方式/交互方式列表以及 Debug 工具栏：
   - 列表横向策略支持 Ignored 收窄，Card 解除强制 minimumSize 约束。
   - 工具栏内部采用 WrappingFlowLayout 换行，防止撑死整页最小宽度。
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout, QWidget

from src.ui.responsive_task_config import WrappingFlowLayout

_QWIDGETSIZE_MAX = 16777215

# 实时截图行并排的最低舒适视口宽度：预览最小 240 + 侧栏约 230 + 页边距。
# 堆叠/恢复之间留 40px 滞回，避免竖滚动条出现/消失引起的来回抖动。
_LOWER_ROW_STACK_BELOW = 560
_LOWER_ROW_UNSTACK_ABOVE = 600

# 状态条省略阈值余量
_STATUS_BAR_RESERVE_WIDTH = 140
_STATUS_BAR_MIN_WIDTH = 100

# StartCard 单行最小容纳宽度估算：
# icon(30)+spacing(16)+title(~120)+statusBar(~120)+spacings(24)+3 buttons(~300)+margins(36) ≈ 650
_START_CARD_SINGLE_ROW_MIN_WIDTH = 680


def _enable_height_for_width(widget):
    policy = widget.sizePolicy()
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)


class StartCardResponsiveController:
    """管理 StartCard 在宽屏单行与窄屏折行之间的自适应切换。"""

    def __init__(self, card):
        self.card = card
        self.buttons = (card.capture_button, card.refresh_button, card.start_button)
        self.status_bar = card.status_bar
        self.icon_label = card.iconLabel
        self.vbox_layout = card.vBoxLayout
        self.header = card.hBoxLayout

        for widget in (self.icon_label, self.status_bar, *self.buttons):
            self.header.removeWidget(widget)
        self.header.removeItem(self.vbox_layout)
        while self.header.count():
            item = self.header.takeAt(0)
            del item

        for label in (card.titleLabel, card.contentLabel):
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
            label.setSizePolicy(policy)
            label.setMinimumWidth(0)

        self.root_widget = QWidget()
        self.root_layout = QVBoxLayout(self.root_widget)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        # 单行布局容器
        self.single_widget = QWidget()
        self.single_layout = QHBoxLayout(self.single_widget)
        self.single_layout.setContentsMargins(0, 0, 20, 0)
        self.single_layout.setSpacing(6)

        # 双行布局容器
        self.double_widget = QWidget()
        self.double_layout = QVBoxLayout(self.double_widget)
        self.double_layout.setContentsMargins(0, 8, 20, 8)
        self.double_layout.setSpacing(6)

        self.double_row1 = QHBoxLayout()
        self.double_row1.setContentsMargins(0, 0, 0, 0)
        self.double_row1.setSpacing(16)

        self.double_row2 = WrappingFlowLayout(spacing=6, alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.double_layout.addLayout(self.double_row1)
        self.double_layout.addLayout(self.double_row2)

        self.root_layout.addWidget(self.single_widget)
        self.root_layout.addWidget(self.double_widget)
        self.header.addWidget(self.root_widget, 1)

        _enable_height_for_width(self.root_widget)
        _enable_height_for_width(self.double_widget)
        _enable_height_for_width(self.card)

        self.card_policy = card.sizePolicy()
        self.card_policy.setHorizontalPolicy(QSizePolicy.Expanding)
        self.card_policy.setVerticalPolicy(QSizePolicy.Preferred)
        self.card.setSizePolicy(self.card_policy)

        self.current_mode = None
        self.set_mode("single")

        # 状态条长文本动态省略
        self.original_set_title = self.status_bar.setTitle
        self.status_bar.setTitle = self._set_title

    def _set_title(self, title):
        self.status_bar._bd2_full_title = title
        self.apply_status_elision(self.card.width())

    def apply_status_elision(self, width):
        full = getattr(self.status_bar, "_bd2_full_title", self.status_bar.title)
        cap = max(_STATUS_BAR_MIN_WIDTH, width - _STATUS_BAR_RESERVE_WIDTH)
        metrics = self.status_bar.titleLabel.fontMetrics()
        if metrics.horizontalAdvance(full) + 50 <= cap:
            text = full
        else:
            text = metrics.elidedText(full, Qt.ElideRight, cap - 50)
        if text == self.status_bar.titleLabel.text():
            return
        self.status_bar.setToolTip(text != full and full or "")
        self.original_set_title(text)

    def set_mode(self, mode):
        if self.current_mode == mode:
            return
        self.current_mode = mode

        if mode == "single":
            self.double_widget.hide()
            self.single_layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)
            self.single_layout.addSpacing(16)
            self.single_layout.addLayout(self.vbox_layout)
            self.single_layout.addSpacing(16)
            self.single_layout.addWidget(self.status_bar, 0, Qt.AlignVCenter)
            self.single_layout.addStretch(1)
            for b in self.buttons:
                self.single_layout.addWidget(b, 0, Qt.AlignVCenter)
            self.single_widget.show()
            self.card.setMinimumHeight(70)
            self.card.setMaximumHeight(70)
        else:
            self.single_widget.hide()
            self.double_row1.addWidget(self.icon_label, 0, Qt.AlignVCenter)
            self.double_row1.addLayout(self.vbox_layout, 1)
            self.double_row1.addWidget(self.status_bar, 0, Qt.AlignVCenter | Qt.AlignRight)
            for b in self.buttons:
                self.double_row2.addWidget(b)
            self.double_widget.show()
            self.card.setMinimumHeight(0)
            self.card.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.card.adjustSize()

    def update_width(self, width):
        required_w = _START_CARD_SINGLE_ROW_MIN_WIDTH
        if hasattr(self.status_bar, "titleLabel") and not self.status_bar.isHidden():
            full = getattr(self.status_bar, "_bd2_full_title", self.status_bar.title)
            status_w = self.status_bar.titleLabel.fontMetrics().horizontalAdvance(full) + 50
            required_w = max(required_w, 480 + status_w)

        if width >= required_w:
            self.set_mode("single")
        else:
            self.set_mode("double")

        self.apply_status_elision(width)


def _wrap_debug_row(start_tab):
    debug_widget = getattr(start_tab, "debug_widget", None)
    debug_layout = getattr(start_tab, "debug_layout", None)
    if debug_widget is None or debug_layout is None:
        return

    container = QWidget()
    flow = WrappingFlowLayout(container, spacing=8, alignment=Qt.AlignLeft | Qt.AlignVCenter)
    while debug_layout.count():
        item = debug_layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            flow.addWidget(widget)
        del item
    debug_layout.addWidget(container, 1)
    _enable_height_for_width(container)
    _enable_height_for_width(debug_widget)


def _shrink_selector_row(start_tab):
    for attr in ("device_list", "capture_list", "interaction_list"):
        widget = getattr(start_tab, attr, None)
        if widget is None:
            continue
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        widget.setSizePolicy(policy)
        widget.setMinimumWidth(0)

    for attr in ("device_container", "capture_container", "interaction_container"):
        container = getattr(start_tab, attr, None)
        if container is None:
            continue
        for layout in (
            container.layout(),
            getattr(container, "cardLayout", None),
            getattr(container, "topLayout", None),
        ):
            if layout is not None:
                layout.setSizeConstraint(QLayout.SetDefaultConstraint)
        container.setMinimumWidth(0)
        policy = container.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        container.setSizePolicy(policy)


class _NarrowLayoutFilter(QObject):
    """视口 resize 时的响应式联动调度。"""

    def __init__(self, start_tab, controller):
        super().__init__(start_tab)
        self._start_tab = start_tab
        self._controller = controller

    def eventFilter(self, _watched, event):
        if event.type() != QEvent.Resize:
            return False
        viewport_w = event.size().width()
        viewport_h = event.size().height()

        # 1. 顶栏根据宽度平滑切换单行/折行与状态省略
        self._controller.update_width(viewport_w)

        # 2. 截图行根据宽度决定并排还是堆叠
        lower_row = getattr(self._start_tab, "live_screenshot_row", None)
        layout = lower_row.layout() if lower_row is not None else None
        if layout is not None:
            direction = layout.direction()
            if direction != QBoxLayout.TopToBottom and viewport_w < _LOWER_ROW_STACK_BELOW:
                layout.setDirection(QBoxLayout.TopToBottom)
            elif direction != QBoxLayout.LeftToRight and viewport_w > _LOWER_ROW_UNSTACK_ABOVE:
                layout.setDirection(QBoxLayout.LeftToRight)

        # 3. 动态计算实时截图框的最大允许高度与宽度，使其随窗口高度顶格放大，多余宽度交给右侧侧栏
        live_card = getattr(self._start_tab, "live_screenshot_card", None)
        if live_card is not None and layout is not None:
            if layout.direction() == QBoxLayout.TopToBottom:
                live_card.setMaximumWidth(_QWIDGETSIZE_MAX)
            else:
                # 顶部占位高度（StartCard + 列表栏 + 外间距边距）约 440px
                start_card = getattr(self._start_tab, "start_card", None)
                selector = getattr(self._start_tab, "selector_widget", None)
                top_h = (start_card.height() if start_card else 70) + (
                    selector.height() if selector else 320
                ) + 50
                avail_h = max(240, viewport_h - top_h - 20)
                # 预览卡片头部文字及间距约占 84px，
                # 画面最高不超过 avail_h - 84，同时设置合理硬上限 450px
                max_preview_h = min(450, max(135, avail_h - 84))
                max_preview_w = int(max_preview_h * 16 / 9)
                live_card.setMaximumWidth(max_preview_w + 32)

        return False


def install_responsive_start_tab(start_tab) -> bool:
    card = getattr(start_tab, "start_card", None)
    if card is None or getattr(card, "_bd2_responsive_installed", False):
        return False

    controller = StartCardResponsiveController(card)
    _wrap_debug_row(start_tab)
    _shrink_selector_row(start_tab)

    view = getattr(start_tab, "view", None)
    if view is not None:
        _enable_height_for_width(view)

    start_tab.viewport().installEventFilter(_NarrowLayoutFilter(start_tab, controller))

    card._bd2_responsive_installed = True
    return True
