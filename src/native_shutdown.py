"""退出前释放原生资源，规避解释器收尾阶段的 0xC0000005 崩溃。

2026-10-13 实测：headless 入口（跑完任务后进程退出）会以 ``0xC0000005`` 崩在
**解释器收尾阶段**——拿不到 Python 栈，因为 faulthandler 在 finalize 时已失效
（``logs/crash-*.log`` 一直是 0 字节正是这个原因）。同一入口重复 12 次的对照实验：

- 直接退出：3~5 次崩溃
- 退出前 ``capture.close()``：0 次
- 退出前清空 OCR 会话：0 次
- 退出前仅执行一次 ``gc.collect()``：0 次
- 修复后（本模块）：``scripts/verify_auto_return.py identify`` 0/12（修复前 7/10）

机制：Windows Graphics Capture 会话与 onnxruntime/openvino 会话这些原生对象
在解释器 finalize 阶段被析构时顺序/时机不安全；趁 Python 仍然健康时释放即可。
常驻的 ``main.py`` 不退出所以碰不到，只有 headless 入口需要在退出前调用本模块。
"""

from __future__ import annotations

import gc


def release_native_resources() -> None:
    """关闭 WGC 抓帧会话、丢弃 OCR 会话，并强制一次垃圾回收。

    每一步都独立保护：任何一步失败都不能影响已经跑完的任务结果，也不能阻止
    后面的清理（尤其 ``gc.collect()`` 本身就是有效手段之一）。
    """
    try:
        from ok import og
    except Exception:
        gc.collect()
        return

    executor = getattr(og, "executor", None)
    failures: list[str] = []

    closer = getattr(getattr(executor, "capture_method", None), "close", None)
    if callable(closer):
        try:
            closer()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"capture.close: {exc}")

    ocr_libs = getattr(executor, "_ocr_lib", None)
    if isinstance(ocr_libs, dict):
        try:
            ocr_libs.clear()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"ocr 会话清理: {exc}")

    gc.collect()

    if failures:
        try:
            from ok import logger

            logger.warning("释放原生资源时出现问题（不影响任务结果）：" + "; ".join(failures))
        except Exception:  # noqa: BLE001
            pass
