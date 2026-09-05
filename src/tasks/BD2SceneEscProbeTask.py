"""Debug-only probe: recognize current scene, test ESC / background click.

Runs as a one-time task:
- captures a frame, runs the same home-page recognition used in production
  (OCR 3-signal + home-button template), and prints OCR boxes in the top-right
  corner (including the "H" shortcut hint rect) to locate the small house icon;
- optionally sends ESC (background PostMessage and/or foreground SendInput);
- optionally performs one pure-background PostMessage click at pixel (X, Y),
  then reports whether the game reached the home page.
"""

from __future__ import annotations

from time import monotonic

from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.task_vision_mixin import TaskVisionMixin


class BD2SceneEscProbeTask(TaskVisionMixin, BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "场景识别+ESC 探针"
        self.description = "识别当前场景，并测试后台/前台 ESC 或后台点击能否返回主页。"
        self.icon = FluentIcon.SEARCH
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.visible = True
        self.default_config.update(
            {
                "ESC 模式": "后台→前台",  # 不发送 / 后台 / 前台 / 后台→前台
                "每次 ESC 后等待秒数": 1.5,
                "保存截图": True,
                "后台点击实现": "postmessage",  # postmessage / operate
                "后台点击 X 像素": -1,  # >=0 时执行点击
                "后台点击 Y 像素": -1,
                "后台点击次数": 1,
                "后台点击间隔秒数": 3.0,
                "点击后主页确认秒数": 8.0,
                "点击后等待秒数": 2.0,
                # 主页按钮模板匹配阈值
                "小屋按钮阈值": 0.78,
                "主页 OCR 阈值": 0.2,
            }
        )
        self._init_vision_state()

    def run(self) -> bool:
        try:
            import sys

            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        mode = str(self.config.get("ESC 模式", "后台→前台"))
        wait = float(self.config.get("每次 ESC 后等待秒数", 1.5))
        save = bool(self.config.get("保存截图", True))
        self.log_info(f"场景识别+ESC 探针开始：ESC 模式={mode}", notify=True)

        lines = [f"ESC 模式={mode}"]
        before = self._snapshot("BEFORE", save)
        lines += before["lines"]
        home = before["home"]

        for esc_mode in self._esc_plan(mode):
            if home:
                break
            sent = self._send_esc(esc_mode, wait)
            print(f"=== ESC mode={esc_mode} sent={sent} ===")
            after = self._snapshot(f"AFTER_{esc_mode}", save)
            lines += ["", f"=== ESC mode={esc_mode} sent={sent} ==="]
            lines += after["lines"]
            home = after["home"]

        click_x = int(self.config.get("后台点击 X 像素", -1))
        click_y = int(self.config.get("后台点击 Y 像素", -1))
        if click_x >= 0 and click_y >= 0:
            click_count = int(self.config.get("后台点击次数", 1))
            for click_index in range(1, click_count + 1):
                sent = self._send_background_click(click_x, click_y, 0.4)
                print(f"=== 后台点击 #{click_index} ({click_x},{click_y}) sent={sent} ===")
                lines += ["", f"=== 后台点击 #{click_index} ({click_x},{click_y}) sent={sent} ==="]
                # 点一次后等加载并确认主页；一旦回主页立刻停，避免在主页上继续误点
                home_wait = float(self.config.get("点击后主页确认秒数", 8.0))
                home = self._wait_for_home(home_wait)
                after = self._snapshot(f"AFTER_click{click_index}", save)
                lines += after["lines"]
                if home:
                    break

        lines += ["", f"最终 home={home}"]
        out = self.write_probe_text("esc_home_probe_latest.txt", lines)
        self.log_completion(f"场景识别+ESC 探针完成：home={home}，详见 {out}")
        return True

    @staticmethod
    def _esc_plan(mode: str) -> list[str]:
        return {
            "不发送": [],
            "后台": ["后台"],
            "前台": ["前台"],
            "后台→前台": ["后台", "前台"],
        }.get(mode, ["后台"])

    def _snapshot(self, tag: str, save: bool) -> dict:
        lines = []
        try:
            frame = self.capture_frame(f"esc_probe_{tag}" if save else None)
        except Exception as exc:
            lines.append(f"[{tag}] capture ERR: {exc}")
            print(f"[{tag}] capture ERR: {exc}")
            return {"home": False, "lines": lines}

        confirmed = False
        left = -1
        p95 = -1.0
        gacha = ""
        try:
            confirmed, left, p95, gacha = self._home_confirmation_signals(frame, "场景探针")
        except Exception as exc:
            lines.append(f"[{tag}] OCR3信号 ERR: {exc}")

        texts: list[str] = []
        top_right_boxes: list[str] = []
        keyword_rects: list[str] = []
        h_rect: str | None = None
        _KEYWORDS = {"员工", "客", "结算", "客人", "常客", "经营管理"}
        try:
            boxes = self.ocr_frame(frame=frame, threshold=0.2)
            frame_h, frame_w = frame.shape[:2]
            for box in boxes:
                name = str(getattr(box, "name", ""))
                if not name:
                    continue
                texts.append(name)
                try:
                    x, y, w, h = int(box.x), int(box.y), int(box.width), int(box.height)
                except Exception:
                    continue
                cx, cy = x + w // 2, y + h // 2
                if name == "H":
                    h_rect = f"H rect=({x},{y},{w},{h}) center=({cx},{cy}) top=({cx},{y})"
                if name in _KEYWORDS:
                    keyword_rects.append(f"{name}@({x},{y},{w},{h}) center=({cx},{cy})")
                # 右上角区域：水平偏右、垂直偏上，用于找“H 提示的小房子”
                if cx / frame_w > 0.55 and cy / frame_h < 0.45:
                    top_right_boxes.append(f"{name}@{cx},{cy}")
        except Exception as exc:
            lines.append(f"[{tag}] 全屏OCR ERR: {exc}")

        btn = -1.0
        btn_passed = False
        btn_pos = None
        btn_center = None
        try:
            from src.tasks.trigger.AutoLoginTask import AutoLoginTask

            btn_obj, spec = AutoLoginTask._match_home_button(self, frame)
            btn = float(btn_obj.score)
            btn_passed = bool(self._passes(btn_obj, spec))
            pos = tuple(getattr(btn_obj, "position", (0, 0)))
            size = tuple(getattr(btn_obj, "size", (0, 0)))
            btn_pos = pos
            if size[0] > 0 and size[1] > 0:
                btn_center = (round(pos[0] + size[0] / 2), round(pos[1] + size[1] / 2))
        except Exception as exc:
            lines.append(f"[{tag}] 主页按钮模板 ERR: {exc}")

        home = bool(confirmed)
        ocr_sample = " ".join(texts)[:180] or "-"
        lines.append(f"[{tag}] home={home} (OCR3={confirmed})")
        lines.append(
            f"[{tag}] left={left} p95={p95:.1f} gacha={gacha!r} "
            f"home_btn={btn:.3f} passed={btn_passed} pos={btn_pos} center={btn_center}"
        )
        lines.append(f"[{tag}] 右上角OCR: {' '.join(top_right_boxes) or '-'}")
        if keyword_rects:
            lines.append(f"[{tag}] 关键字框: {' | '.join(keyword_rects)}")
        if h_rect:
            lines.append(f"[{tag}] {h_rect}")
        lines.append(f"[{tag}] ocr={ocr_sample}")
        print(
            f"[{tag}] home={home} left={left} p95={p95:.1f} gacha={gacha!r} "
            f"home_btn={btn:.3f} passed={btn_passed} pos={btn_pos} center={btn_center}"
        )
        print(f"[{tag}] 右上角OCR: {' '.join(top_right_boxes) or '-'}")
        if keyword_rects:
            print(f"[{tag}] 关键字框: {' | '.join(keyword_rects)}")
        if h_rect:
            print(f"[{tag}] {h_rect}")
        print(f"[{tag}] ocr={ocr_sample}")
        return {"home": home, "lines": lines}

    def _wait_for_home(self, timeout: float) -> bool:
        """轮询主页三信号，等待加载完成并确认回到主页。"""
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            try:
                frame = self.capture_frame()
                confirmed, _left, _p95, _gacha = self._home_confirmation_signals(
                    frame, "点击后主页"
                )
                if confirmed:
                    return True
            except Exception as exc:
                print(f"等待主页时出错: {exc}")
            self.sleep(1.0)
        return False

    def _send_esc(self, mode: str, wait: float) -> bool:
        try:
            if mode == "前台":
                interaction = getattr(self.executor, "interaction", None)
                hwnd_window = (
                    getattr(interaction, "hwnd_window", None)
                    if interaction is not None
                    else None
                )
                if hwnd_window is not None:
                    hwnd_window.bring_to_front()
                    self.sleep(0.4)
                import pydirectinput

                pydirectinput.press("esc")
                self.sleep(wait)
            else:  # 后台
                self.send_key("esc", after_sleep=wait)
            return True
        except Exception as exc:
            self.log_warning(f"发送 ESC 失败 mode={mode}: {exc}")
            print(f"ESC send error {mode}: {exc}")
            return False

    def _send_background_click(self, x: int, y: int, wait: float) -> bool:
        """点击实现可选：postmessage=官方后台测试路径；operate=正式任务 operate_click 路径。"""
        impl = str(self.config.get("后台点击实现", "postmessage"))
        try:
            if impl == "operate":
                # 走真实任务的 operate_click(相对坐标)：operate 锁 + 正式版 click 路径
                self.operate_click(
                    x / max(1.0, float(self.width)),
                    y / max(1.0, float(self.height)),
                    name=f"operate_click {x},{y}",
                    after_sleep=wait,
                    down_time=0.02,
                )
                return True
            interaction = getattr(self.executor, "interaction", None)
            if interaction is None:
                print("no interaction for background click")
                return False
            from ok.device.intercation import PostMessageInteraction

            PostMessageInteraction.click(
                interaction,
                int(x),
                int(y),
                move_back=False,
                name=f"bd2_background_mouse_click {x},{y}",
                down_time=0.02,
                move=True,
                key="left",
            )
            self.sleep(wait)
            return True
        except Exception as exc:
            self.log_warning(f"后台点击失败 ({x},{y}) impl={impl}: {exc}")
            print(f"后台点击失败 ({x},{y}) impl={impl}: {exc}")
            return False
