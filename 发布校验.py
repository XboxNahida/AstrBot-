"""在无生产导入、无网络的环境中执行导出版实际更新方法。"""

import ast
import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv.pop(1)).resolve()


@dataclass
class Result:
    data: object = None
    message: str = ""
    status: str = "ok"


class UpdateServiceError(Exception):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


def methods(relative, class_name, names, namespace=None):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    if class_name:
        cls, = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    else:
        cls = tree
    selected = [copy.deepcopy(node) for node in cls.body if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names]
    assert len(selected) == len(names)
    for node in selected:
        node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    scope = {"VERSION": "4.27.2", "UpdateServiceResult": Result, "UpdateServiceError": UpdateServiceError, **(namespace or {})}
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), "exec"), scope)
    return scope


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_cannot_bypass_plugin_update_block(self):
        scope = methods("astrbot/cli/commands/cmd_plug.py", None, {"update"},
                        {"click": SimpleNamespace(ClickException=UpdateServiceError)})
        for name in (None, "existing-plugin"):
            with self.assertRaisesRegex(UpdateServiceError, "关闭内置更新"):
                scope["update"](name, None)

    async def test_core_and_plugin_updates_are_blocked(self):
        for relative, cls in [("astrbot/core/updater.py", "AstrBotUpdater"),
                               ("astrbot/core/star/updater.py", "_PluginUpdater")]:
            scope = methods(relative, cls, {"update"})
            with self.assertRaisesRegex(RuntimeError, "https://github.com/AstrBotDevs/AstrBot"):
                await scope["update"](object(), object())

    async def test_check_never_contacts_release_api(self):
        scope = methods("astrbot/core/updater.py", "AstrBotUpdater", {"check_update", "get_releases"})
        self.assertIsNone(await scope["check_update"](object()))
        self.assertEqual(await scope["get_releases"](object()), [])

    async def test_dashboard_reads_only_bundled_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            scope = methods("astrbot/core/updater.py", "AstrBotUpdater", {"ensure_dashboard"},
                            {"_get_bundled_dist_path": lambda: folder,
                             "_is_dist_compatible": lambda path, version: (path / "index.html").is_file()})
            with self.assertRaisesRegex(RuntimeError, "本地面板文件缺失"):
                await scope["ensure_dashboard"](object())
            (folder / "index.html").write_text("合成面板", encoding="utf-8")
            self.assertEqual(await scope["ensure_dashboard"](object()), folder)

    async def test_web_api_rejects_before_scheduling_any_task(self):
        scope = methods("astrbot/dashboard/services/update_service.py", "UpdateService",
                        {"update_project", "update_dashboard", "check_update", "get_releases"})
        for name, args in [("update_project", ({"reboot": True},)), ("update_dashboard", ())]:
            with self.assertRaises(UpdateServiceError) as error:
                await scope[name](object(), *args)
            self.assertEqual(error.exception.code, "updates_disabled")
        for kind in (None, "dashboard", "core"):
            result = await scope["check_update"](object(), kind)
            self.assertFalse(result.data["has_new_version"])
            self.assertTrue(result.data["updates_disabled"])
            self.assertIn("关闭内置更新", result.message)
        self.assertEqual((await scope["get_releases"](object())).data, [])


class InitializeTests(unittest.TestCase):
    def test_first_run_and_repeated_run_do_not_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "初始化配置.py"
            shutil.copy2(ROOT.parent / "初始化配置.py", script)
            presets = root / "发布预设/data"
            (presets / "config").mkdir(parents=True)
            (presets / "cmd_config.json").write_text('{"model":"gemini"}', encoding="utf-8")
            (presets / "config/plugin.json").write_text('{"persona":"晚柠"}', encoding="utf-8")
            runtime = root / "AstrBot-master/data"
            runtime.mkdir(parents=True)
            (runtime / "cmd_config.json").write_text('{"key":"synthetic-user-secret"}', encoding="utf-8")
            for _ in range(2):
                result = subprocess.run([sys.executable, str(script)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((runtime / "cmd_config.json").read_text(encoding="utf-8"), '{"key":"synthetic-user-secret"}')
                self.assertEqual((runtime / "config/plugin.json").read_text(encoding="utf-8"), '{"persona":"晚柠"}')

    def test_missing_preset_does_not_create_default_runtime_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "初始化配置.py"
            shutil.copy2(ROOT.parent / "初始化配置.py", script)
            result = subprocess.run([sys.executable, str(script)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "AstrBot-master/data").exists())


if __name__ == "__main__":
    unittest.main()
