from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace

from .test_tts_language_provider_routing import _FakeTtsProvider, _RoutingHarness


class TtsSingingProviderRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _RoutingHarness()
        self.harness.tts_voice_language = "zh"
        self.xiaoqi = _FakeTtsProvider("xiaoqi", "小七说话", "mimo-v2.5-tts-voiceclone")
        self.wanning = _FakeTtsProvider("wanning", "晚柠说话", "mimo-v2.5-tts")
        self.singer_qi = _FakeTtsProvider("xiaoqi-sing", "小七唱歌", "mimo-v2.5-tts")
        self.singer_ning = _FakeTtsProvider("wanning-sing", "晚柠唱歌", "mimo-v2.5-tts")
        for speaker, singer in (
            (self.xiaoqi, self.singer_qi),
            (self.wanning, self.singer_ning),
        ):
            speaker.provider_config.update(
                {
                    "type": "mimo_tts_api",
                    "mimo-tts-singing-provider-id": singer.provider_config["id"],
                }
            )
            singer.provider_config["type"] = "mimo_tts_api"
        self.harness.context.providers.extend(
            [self.xiaoqi, self.wanning, self.singer_qi, self.singer_ning]
        )
        self.default_config = {
            "provider_tts_settings": {"enable": True, "provider_id": "tts-default"}
        }
        self.bot_configs = {
            "Nahida": {
                "provider_tts_settings": {"enable": True, "provider_id": "xiaoqi"}
            },
            "likh": {
                "provider_tts_settings": {"enable": True, "provider_id": "wanning"}
            },
        }
        self.harness.context.get_config = lambda umo=None: self.bot_configs.get(
            str(umo).split(":")[0], self.default_config
        )

    def test_explicit_singing_routes_each_bot_in_groups_and_private(self) -> None:
        original = deepcopy([p.provider_config for p in self.harness.context.providers])
        for bot, singer in (("Nahida", self.singer_qi), ("likh", self.singer_ning)):
            for scope in ("GroupMessage", "FriendMessage"):
                for text in ("用你的音色唱一首中文歌", "重新唱一下", "唱一小段英文歌"):
                    with self.subTest(bot=bot, scope=scope, text=text):
                        event = SimpleNamespace(
                            unified_msg_origin=f"{bot}:{scope}:same-id",
                            message_str=text,
                        )
                        self.assertIs(
                            self.harness._resolve_tts_synthesis_provider(
                                event, self.harness.default
                            ),
                            singer,
                        )
        self.assertEqual(
            original, [p.provider_config for p in self.harness.context.providers]
        )
        self.assertIs(self.harness.context.current, self.harness.default)

    def test_normal_speech_and_music_playback_keep_speaking_provider(self) -> None:
        for bot, speaker in (("Nahida", self.xiaoqi), ("likh", self.wanning)):
            for scope in ("GroupMessage", "FriendMessage"):
                for text in (
                    "用语音回答我",
                    "来一首中文歌",
                    "把歌词念一下",
                    "这首歌叫什么",
                ):
                    with self.subTest(bot=bot, scope=scope, text=text):
                        event = SimpleNamespace(
                            unified_msg_origin=f"{bot}:{scope}:same-id",
                            message_str=text,
                        )
                        self.assertIs(
                            self.harness._resolve_tts_synthesis_provider(
                                event, self.harness.default
                            ),
                            speaker,
                        )

    def test_confirmed_singing_marker_selects_the_same_provider(self) -> None:
        event = SimpleNamespace(
            unified_msg_origin="Nahida:GroupMessage:group",
            message_str="再来一次",
            _private_companion_singing_requested=True,
        )
        self.assertIs(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default),
            self.singer_qi,
        )

    def test_missing_singer_does_not_fall_back_to_clone_or_another_bot(self) -> None:
        self.harness.context.providers.remove(self.singer_qi)
        event = SimpleNamespace(
            unified_msg_origin="Nahida:GroupMessage:group", message_str="唱一首歌"
        )
        self.assertIsNone(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default)
        )

    def test_clone_or_voice_design_cannot_be_selected_as_singer(self) -> None:
        event = SimpleNamespace(
            unified_msg_origin="Nahida:FriendMessage:user", message_str="唱一下"
        )
        for model in ("mimo-v2.5-tts-voiceclone", "mimo-v2.5-tts-voicedesign"):
            with self.subTest(model=model):
                self.singer_qi.model_name = model
                self.singer_qi.provider_config["model"] = model
                self.assertIsNone(
                    self.harness._resolve_tts_synthesis_provider(
                        event, self.harness.default
                    )
                )

    def test_disabled_singer_does_not_fall_back_to_speaking(self) -> None:
        self.singer_qi.provider_config["enable"] = False
        event = SimpleNamespace(
            unified_msg_origin="Nahida:GroupMessage:group", message_str="唱一首歌"
        )
        self.assertIsNone(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default)
        )

    def test_unconfigured_clone_does_not_pretend_to_support_singing(self) -> None:
        self.xiaoqi.provider_config.pop("mimo-tts-singing-provider-id")
        event = SimpleNamespace(
            unified_msg_origin="Nahida:GroupMessage:group", message_str="唱一首歌"
        )
        self.assertIsNone(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default)
        )

    def test_base_mimo_can_sing_without_a_separate_binding(self) -> None:
        self.wanning.provider_config.pop("mimo-tts-singing-provider-id")
        event = SimpleNamespace(
            unified_msg_origin="likh:GroupMessage:group", message_str="唱一首歌"
        )
        self.assertIs(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default),
            self.wanning,
        )

    def test_bot_tts_disable_is_not_bypassed_by_singing(self) -> None:
        self.bot_configs["Nahida"]["provider_tts_settings"]["enable"] = False
        event = SimpleNamespace(
            unified_msg_origin="Nahida:GroupMessage:group", message_str="唱一首歌"
        )
        self.assertIsNone(
            self.harness._resolve_tts_synthesis_provider(event, self.harness.default)
        )


if __name__ == "__main__":
    unittest.main()
