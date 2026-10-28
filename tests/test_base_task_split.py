"""P4：``BaseBD2Task`` 拆分为两个 mixin 后的接口与兼容性守卫。

拆分前（基线 ``7f3f348``）``BaseBD2Task`` 的类字典含 51 个方法 + ``DEFAULT_MOVE``；
拆分后这些名字恰好分布在 ``BaseBD2Task``（初始化/状态输出/生命周期）、
``DeviceOpsMixin``（工具/设备操作）与 ``CartridgeHomeMixin``（卡带/主页业务）三个
类字典中，且 ``dir(BaseBD2Task)`` 与拆分前完全一致（MRO 解析后无缺失/无多出）。
本文件把这份接口契约固定下来，并保证两个 mixin 不反向 import ``BaseBD2Task``。
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.tasks.BaseBD2Task import (
    PROBE_OUTPUT_DIR,
    RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS,
    RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
    TEMPLATE_DIR,
    BaseBD2Task,
    RecentPvpCartridgeMatch,
    green_mask_from_template,
    task_info_snapshot,
)
from src.tasks.cartridge_home_mixin import CartridgeHomeMixin
from src.tasks.device_ops_mixin import DeviceOpsMixin

ROOT = Path(__file__).resolve().parents[1]

DEVICE_OPS_METHODS = frozenset(
    {
        "capture_frame",
        "capture_method_name",
        "click",
        "drag_client",
        "find_green_mask_features",
        "find_one_green_mask",
        "green_mask",
        "main_viewport",
        "ocr_frame",
        "operate",
        "operate_click",
        "save_frame",
        "scroll_client",
        "write_probe_text",
        "_click_log_message",
        "_sleep_after_recognition",
    }
)

CARTRIDGE_HOME_METHODS = frozenset(
    {
        "attempt_return_home",
        "auto_return_main_home",
        "clear_temporary_home_announcement_if_needed",
        "open_cartridge_quick_switcher",
        "_check_action_interval",
        "_click_gacha_detail_close",
        "_click_top_left_back",
        "_find_ocr_box",
        "_handle_recent_cartridge_special_pages",
        "_load_recent_pvp_cartridge_template",
        "_match_recent_pvp_cartridge",
        "_ocr_box_center",
        "_pvp_special_page_action",
        "_pvp_special_page_ocr_boxes",
        "_recent_cartridge_is_pvp",
        "_recent_cartridge_ocr_boxes",
        "_recognize_page",
        "_retry_recent_cartridge_entry",
        "_save_flow_diagnostic",
    }
)

BASE_TASK_METHODS = frozenset(
    {
        "__init__",
        "info_add",
        "info_add_to_list",
        "info_clear",
        "info_incr",
        "info_set",
        "info_snapshot",
        "is_main",
        "log_completion",
        "mark_logged_in",
        "run_with_interval",
        "submit_periodic_task",
        "thread_pool_executor",
        "verified_transition",
        "wait_main",
        "_task_info_lock",
    }
)


def _underlying(attribute):
    """``property``/``staticmethod``/``classmethod``/绑定方法背后的函数对象。"""
    if isinstance(attribute, property):
        return attribute.fget
    return getattr(attribute, "__func__", attribute)


def _interface_names(class_) -> set:
    """类字典里的名字；``__init__`` 计入，其余纯 dunder 不计。"""
    return {
        name
        for name in vars(class_)
        if name == "__init__" or not (name.startswith("__") and name.endswith("__"))
    }


class BaseTaskSplitTest(unittest.TestCase):
    def test_interface_set_is_the_union_of_the_three_class_dicts(self):
        expected = (
            DEVICE_OPS_METHODS
            | CARTRIDGE_HOME_METHODS
            | BASE_TASK_METHODS
            | {"DEFAULT_MOVE"}
        )
        actual = (
            _interface_names(DeviceOpsMixin)
            | _interface_names(CartridgeHomeMixin)
            | _interface_names(BaseBD2Task)
        )
        self.assertEqual(set(), expected ^ actual, "拆分前后接口集合必须一致")

    def test_moved_methods_live_on_the_new_mixins(self):
        for mixin, module_name, names in (
            (DeviceOpsMixin, "src.tasks.device_ops_mixin", DEVICE_OPS_METHODS),
            (CartridgeHomeMixin, "src.tasks.cartridge_home_mixin", CARTRIDGE_HOME_METHODS),
        ):
            for name in sorted(names):
                with self.subTest(mixin=mixin.__name__, method=name):
                    self.assertIn(name, vars(mixin), "方法必须定义在该 mixin 上")
                    self.assertNotIn(name, vars(BaseBD2Task), "基类不再自带该方法")
                    raw = vars(mixin)[name]
                    self.assertIs(
                        _underlying(getattr(BaseBD2Task, name)),
                        _underlying(raw),
                    )
                    self.assertEqual(module_name, _underlying(raw).__module__)

    def test_base_task_keeps_state_output_init_and_lifecycle(self):
        for name in sorted(BASE_TASK_METHODS):
            with self.subTest(method=name):
                self.assertIn(name, vars(BaseBD2Task))
        self.assertFalse(vars(BaseBD2Task)["DEFAULT_MOVE"])

    def test_mixins_come_before_ok_base_task_in_mro(self):
        mro = BaseBD2Task.__mro__
        self.assertEqual(
            ["BaseBD2Task", "DeviceOpsMixin", "CartridgeHomeMixin", "BaseTask"],
            [cls.__name__ for cls in mro[:4]],
        )
        names = [cls.__name__ for cls in mro]
        self.assertLess(names.index("CartridgeHomeMixin"), names.index("BaseTask"))
        self.assertTrue(issubclass(BaseBD2Task, DeviceOpsMixin))
        self.assertTrue(issubclass(BaseBD2Task, CartridgeHomeMixin))

    def test_mixins_do_not_import_base_bd2_task(self):
        for file_name in ("device_ops_mixin.py", "cartridge_home_mixin.py"):
            path = ROOT / "src" / "tasks" / file_name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
                elif isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
            for module in imported:
                with self.subTest(file=file_name, module=module):
                    self.assertNotIn("BaseBD2Task", module, "mixin 不得反向 import 基类模块")

    def test_compatibility_imports_from_base_bd2_task_module(self):
        self.assertEqual(3, RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS)
        self.assertEqual(
            "cartridge-image2-left-lower-cutout.png",
            RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
        )
        self.assertEqual("probe_outputs", str(PROBE_OUTPUT_DIR))
        self.assertEqual("template-assets", TEMPLATE_DIR.name)
        self.assertTrue(callable(green_mask_from_template))
        self.assertTrue(callable(task_info_snapshot))
        # 阈值随之搬到 cartridge_home_mixin，默认构造的默认值不通过判定。
        self.assertFalse(RecentPvpCartridgeMatch().passed)

    def test_private_entry_points_still_work_on_object_new_stubs(self):
        """既有测试惯用 ``object.__new__`` 直接调私有方法，拆分后仍须可用。"""
        task = object.__new__(BaseBD2Task)
        self.assertEqual(
            "x: client=(959,539), relative=(0.500000,0.500000)",
            BaseBD2Task._click_log_message(0.5, 0.5, 1918, 1079, "x"),
        )
        box = SimpleNamespace(x=10, y=20, width=40, height=40)
        self.assertEqual((30.0, 40.0), BaseBD2Task._ocr_box_center(box))
        task.info = {"状态": "运行中"}
        self.assertEqual({"状态": "运行中"}, task_info_snapshot(task))


if __name__ == "__main__":
    unittest.main()
