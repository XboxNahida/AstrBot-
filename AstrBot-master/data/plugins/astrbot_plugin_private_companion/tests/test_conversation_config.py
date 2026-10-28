import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class ConversationConfigTests(unittest.TestCase):
    def test_official_settings_default_off_and_providers_visible_in_both_modes(self):
        schema = json.loads(
            (Path(__file__).parents[1] / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )
        items = schema["model_assignment_config"]["items"]
        for key in ("enable_conversation_model_routing", "enable_short_term_emotion"):
            self.assertIs(items[key]["default"], False)
        for role in ("CHAT", "CHAT_FALLBACK", "TASK", "TASK_FALLBACK", "HUB"):
            entry = items[f"CONVERSATION_{role}_PROVIDER_ID"]
            self.assertEqual("select_provider", entry["_special"])
            self.assertEqual("", entry["default"])
            self.assertNotIn("condition", entry)

    def test_panel_runtime_updates_independent_conversation_settings(self):
        api = object.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace()
        for role in ("CHAT", "CHAT_FALLBACK", "TASK", "TASK_FALLBACK", "HUB"):
            api._apply_config_value(f"CONVERSATION_{role}_PROVIDER_ID", role.lower())
            self.assertEqual(
                role.lower(),
                getattr(api.plugin, f"conversation_{role.lower()}_provider_id"),
            )
        for key in ("enable_conversation_model_routing", "enable_short_term_emotion"):
            api._apply_config_value(key, "false")
            self.assertIs(getattr(api.plugin, key), False)
            api._apply_config_value(key, True)
            self.assertIs(getattr(api.plugin, key), True)

    def test_quick_mode_preserves_independent_roles(self):
        from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.config = {}
        plugin.provider_config_mode = "quick"
        for role in ("chat", "chat_fallback", "task", "task_fallback", "hub"):
            setattr(plugin, f"conversation_{role}_provider_id", role)
        plugin._apply_quick_provider_defaults()
        for role in ("chat", "chat_fallback", "task", "task_fallback", "hub"):
            self.assertEqual(role, getattr(plugin, f"conversation_{role}_provider_id"))
