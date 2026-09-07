"""启动页（StartTab）窄宽度响应式重排。

窗口最小宽度 600 下，页面视口只剩三百多像素；而启动页多行的最小宽度之和
远超这个值，ScrollArea 关闭了横向滚动条，超宽的 view 整体被右端裁切——
表现就是 StartCard 上截图/刷新/启动三个按钮被遮挡。

这里从四个来源把最小宽度降下来：

1. StartCard：标题/版本与状态条留第一行（两者都可收窄：标题裁切、状态条
   按视口宽度省略为 "…"），三个按钮挪到第二行右对齐流式布局，放不下时
   自动换行；卡片放开固定 70px 高度并打通 height-for-width。
2. Debug 卡片的按钮行：改为流式布局，窄了换行而不是撑宽整页。
3. 选择窗口/截图方式/交互方式三个列表：横向策略改 Ignored 并解除
   Card 的 SetMinimumSize 约束，允许随视口收窄（列表内容自身可裁切）。
4. 实时截图行：视口宽度不足时由左右并排切换为上下堆叠。

状态条省略与截图行堆叠都由 ScrollArea 视口的 resize 驱动：监听控件自身
会自锁——水平方向的最小宽度使控件永远收不到触发切换的那次 resize。
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLayout, QSizePolicy, QVBoxLayout, QWidget

from src.ui.responsive_task_config import WrappingFlowLayout

_QWIDGETSIZE_MAX = 16777215

# 视口宽度减去该余量即状态条允许的最大宽度：页边距、图标、行间距与标题列。
_STATUS_BAR_RESERVE_WIDTH = 140
_STATUS_BAR_MIN_WIDTH = 100

# 实时截图行并排的最低舒适视口宽度：预览最小 240 + 侧栏约 230 + 页边距。
# 堆叠/恢复之间留 40px 滞回，避免竖滚动条出现/消失引起的来回抖动。
_LOWER_ROW_STACK_BELOW = 560
_LOWER_ROW_UNSTACK_ABOVE = 600


def _enable_height_for_width(widget):
    policy = widget.sizePolicy()
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)


def _reflow_start_card(card):
    buttons = (card.capture_button, card.refresh_button, card.start_button)
    status_bar = card.status_bar
    header = card.hBoxLayout

    for widget in (card.iconLabel, status_bar, *buttons):
        header.removeWidget(widget)
    header.removeItem(card.vBoxLayout)
    while header.count():
        # 剩余的都是固定间距与拉伸占位，随旧行布局一并丢弃。
        item = header.takeAt(0)
        del item

    # 极窄时标题/版本可以被裁切，但不能把按钮挤出卡片。
    for label in (card.titleLabel, card.contentLabel):
        policy = label.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        label.setSizePolicy(policy)
        label.setMinimumWidth(0)

    container = QWidget()
    container_layout = QVBoxLayout(container)
    # header 自带左边距 16；原行布局在启动按钮后留了 20px 右间距。
    container_layout.setContentsMargins(0, 10, 20, 10)
    container_layout.setSpacing(8)

    top_row = QHBoxLayout()
    top_row.setContentsMargins(0, 0, 0, 0)
    top_row.setSpacing(16)
    top_row.addWidget(card.iconLabel, 0, Qt.AlignVCenter)
    top_row.addLayout(card.vBoxLayout, 1)
    top_row.addWidget(status_bar, 0, Qt.AlignVCenter | Qt.AlignRight)
    container_layout.addLayout(top_row)

    button_row = WrappingFlowLayout(spacing=6, alignment=Qt.AlignRight | Qt.AlignVCenter)
    for button in buttons:
        button_row.addWidget(button)
    container_layout.addLayout(button_row)

    header.addWidget(container, 1)

    # SettingCard 固定 70px 高会裁掉换行后的第二排按钮，放开高度限制。
    card.setMinimumHeight(0)
    card.setMaximumHeight(_QWIDGETSIZE_MAX)
    card_policy = card.sizePolicy()
    card_policy.setHorizontalPolicy(QSizePolicy.Expanding)
    card_policy.setVerticalPolicy(QSizePolicy.Preferred)
    card.setSizePolicy(card_policy)

    # height-for-width 需要逐层打通，外层布局才会按宽度重新求高度。
    _enable_height_for_width(container)
    _enable_height_for_width(card)

    # StatusBar 按文本长度定死宽度，长状态文本（如窗口未前置的暂停提示）
    # 自己就能把整行撑出视口。记录全文，实际显示宽度由
    # _NarrowLayoutFilter 按视口宽度重新省略。
    original_set_title = status_bar.setTitle

    def set_title(title):
        status_bar._bd2_full_title = title
        _apply_status_elision(status_bar, card.width())

    def _apply_status_elision(bar, width):
        full = getattr(bar, "_bd2_full_title", bar.title)
        cap = max(_STATUS_BAR_MIN_WIDTH, width - _STATUS_BAR_RESERVE_WIDTH)
        metrics = bar.titleLabel.fontMetrics()
        if metrics.horizontalAdvance(full) + 50 <= cap:
            text = full
        else:
            text = metrics.elidedText(full, Qt.ElideRight, cap - 50)
        if text == bar.titleLabel.text():
            return
        bar.setToolTip(text != full and full or "")
        original_set_title(text)

    status_bar.setTitle = set_title
    card._bd2_apply_status_elision = lambda width: _apply_status_elision(status_bar, width)


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
        # Card 的 SetMinimumSize 约束会把布局最小宽度写死成控件的
        # minimumSize，解除后才能随视口收窄。
        for layout in (container.layout(), getattr(container, "cardLayout", None),
                       getattr(container, "topLayout", None)):
            if layout is not None:
                layout.setSizeConstraint(QLayout.SetDefaultConstraint)
        container.setMinimumWidth(0)
        policy = container.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        container.setSizePolicy(policy)


class _NarrowLayoutFilter(QObject):
    """视口 resize 时联动：状态条按宽度省略 + 实时截图行并排/堆叠切换。"""

    def __init__(self, start_tab):
        super().__init__(start_tab)
        self._start_tab = start_tab

    def eventFilter(self, _watched, event):
        if event.type() != QEvent.Resize:
            return False
        width = event.size().width()

        apply_elision = getattr(self._start_tab.start_card, "_bd2_apply_status_elision", None)
        if apply_elision is not None:
            apply_elision(width)

        lower_row = getattr(self._start_tab, "live_screenshot_row", None)
        layout = lower_row.layout() if lower_row is not None else None
        if layout is not None:
            direction = layout.direction()
            if direction != QBoxLayout.TopToBottom and width < _LOWER_ROW_STACK_BELOW:
                layout.setDirection(QBoxLayout.TopToBottom)
            elif direction != QBoxLayout.LeftToRight and width > _LOWER_ROW_UNSTACK_ABOVE:
                layout.setDirection(QBoxLayout.LeftToRight)
        return False


def install_responsive_start_tab(start_tab) -> bool:
    card = getattr(start_tab, "start_card", None)
    if card is None or getattr(card, "_bd2_responsive_installed", False):
        return False

    _reflow_start_card(card)
    _wrap_debug_row(start_tab)
    _shrink_selector_row(start_tab)

    view = getattr(start_tab, "view", None)
    if view is not None:
        _enable_height_for_width(view)

    start_tab.viewport().installEventFilter(_NarrowLayoutFilter(start_tab))

    card._bd2_responsive_installed = True
    return True
