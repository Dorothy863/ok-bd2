import unittest

from ok import og

from src.native_shutdown import release_native_resources


class _ExplodingCapture:
    def close(self) -> None:
        raise RuntimeError("capture close failed")


class _FakeExecutor:
    capture_method = _ExplodingCapture()
    _ocr_lib = {"default": object()}


class NativeShutdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original = getattr(og, "executor", None)

    def tearDown(self) -> None:
        og.executor = self._original

    def test_release_is_safe_without_executor(self):
        og.executor = None
        release_native_resources()  # 不应抛异常

    def test_release_closes_capture_and_drops_ocr_sessions(self):
        _FakeExecutor._ocr_lib = {"default": object()}
        og.executor = _FakeExecutor()

        release_native_resources()  # capture.close() 抛异常也必须被吞掉

        self.assertEqual({}, _FakeExecutor._ocr_lib)


if __name__ == "__main__":
    unittest.main()
