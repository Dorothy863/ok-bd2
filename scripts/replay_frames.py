"""对“录制的真实客户端帧”回放页面识别与自动返回主页决策（无需客户端在线）。

用途：P7 实机验收的离线前置验证。真实帧 + 真实 OCR 引擎 + 真实的
``identify_page`` / ``auto_return_main_home`` 实现，只有“截图”与“点击”是替身：
截图返回录制帧，点击只记录坐标不真正发送。

用法：
    uv run python scripts/replay_frames.py probe_outputs            # 仅识别
    uv run python scripts/replay_frames.py probe_outputs --replay    # 识别 + 决策回放

报告写到 ``probe_outputs/replay_report.txt``（UTF-8；该目录已被 .gitignore 忽略，
帧里含账号内文本，不入库）。控制台是 GBK，因此只打印 ASCII 摘要。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
from ok import Box  # noqa: E402
from onnxocr.onnx_paddleocr import ONNXPaddleOcr  # noqa: E402

from src.tasks.cartridge_home_mixin import CartridgeHomeMixin  # noqa: E402
from src.tasks.page_recognition import identify_page  # noqa: E402

# 与生产一致：``BaseBD2Task._recognize_page`` 用 threshold=0.2、target_height=0。
OCR_THRESHOLD = 0.2


def _boxes_from_ocr_result(items) -> list[Box]:
    boxes = []
    for item in items:
        points, (text, score) = item[0], item[1]
        if float(score) < OCR_THRESHOLD:
            continue
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        left, top = min(xs), min(ys)
        boxes.append(
            Box(
                name=str(text),
                x=int(round(left)),
                y=int(round(top)),
                width=int(round(max(xs) - left)),
                height=int(round(max(ys) - top)),
            )
        )
    return boxes


class _FrameReplayTask(CartridgeHomeMixin):
    """只提供 ``auto_return_main_home`` 需要的替身面（页面判定走真实 mixin 实现）。"""

    def __init__(self, frame, boxes, config):
        self._frame = frame
        self._boxes = boxes
        self.config = config
        self.clicks: list[tuple] = []
        self.infos: list[tuple] = []
        self.logs: list[str] = []
        self.warnings: list[str] = []

    def capture_frame(self):
        return self._frame

    def ocr(self, **_kwargs):
        return list(self._boxes)

    def operate_click(self, x=-1, y=-1, **kwargs):
        self.clicks.append((x, y, kwargs.get("name"), kwargs.get("after_sleep")))

    def sleep(self, *_args, **_kwargs):
        return None

    def info_set(self, *args):
        self.infos.append(args)

    def log_info(self, message):
        self.logs.append(str(message))

    def log_warning(self, message, **_kwargs):
        self.warnings.append(str(message))

    def operate(self, func, **_kwargs):
        return func()


def replay(frame, boxes, config):
    task = _FrameReplayTask(frame, boxes, config)
    try:
        result = task.auto_return_main_home()
    except Exception as exc:  # noqa: BLE001 - 诊断输出需要看到任何异常
        return {"error": f"{type(exc).__name__}: {exc}", "clicks": task.clicks}
    return {
        "result": result,
        "clicks": [{"x": c[0], "y": c[1], "name": c[2], "after_sleep": c[3]} for c in task.clicks],
        "logs": task.logs,
        "warnings": task.warnings,
        "infos": [(str(k), str(v)) for k, v in task.infos],
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    target = Path(args[0]) if args else Path("probe_outputs")
    do_replay = "--replay" in flags

    frames = sorted(target.glob("*.png")) if target.is_dir() else [target]
    frames = [p for p in frames if cv2.imread(str(p)) is not None]
    if not frames:
        print("no readable frames found")
        return 2

    # 空 config：``auto_return_main_home`` 的每个配置键都带与 default_config 相同的
    # 内联默认值，因此等价于“生产默认配置、不含用户覆盖”。
    config: dict = {}

    ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True)
    lines: list[str] = []
    for path in frames:
        frame = cv2.imread(str(path))
        result = ocr.ocr(frame, cls=False)
        boxes = _boxes_from_ocr_result(result[0] if result else [])
        recognition = identify_page(frame, boxes)
        lines.append(f"### {path.name}  {frame.shape[1]}x{frame.shape[0]}  boxes={len(boxes)}")
        lines.append(f"page = {recognition.page.value}")
        lines.append(f"home_label_seen = {recognition.signals.home_label_seen}")
        lines.append(
            f"home_announcement_seen = {recognition.signals.home_announcement_seen}"
        )
        lines.append(f"gacha_detail_keywords = {recognition.signals.gacha_detail_keywords}")
        lines.append(f"house_hint_center = {recognition.signals.house_hint_center}")
        lines.append(f"detail_close_center = {recognition.signals.detail_close_center}")
        lines.append("ocr_labels = " + " | ".join(b.name for b in boxes))
        if do_replay:
            outcome = replay(frame, boxes, config)
            lines.append(f"replay = {outcome.get('result', outcome.get('error'))}")
            for click in outcome.get("clicks", []):
                lines.append(f"  click {click}")
            for message in outcome.get("logs", [])[:6]:
                lines.append(f"  log: {message}")
            for message in outcome.get("warnings", [])[:4]:
                lines.append(f"  warn: {message}")
        lines.append("")
        print(f"OK {path.name}: page={recognition.page.value} boxes={len(boxes)}")

    report = Path("probe_outputs") / "replay_report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("report:", report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
