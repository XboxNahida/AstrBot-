import asyncio
import base64

from astrbot.core.utils.datetime_utils import generate_timestamp_id

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter
from .mimo_api_common import (
    DEFAULT_MIMO_API_BASE,
    DEFAULT_MIMO_TTS_MODEL,
    DEFAULT_MIMO_TTS_SEED_TEXT,
    DEFAULT_MIMO_TTS_VOICE,
    MiMoAPIError,
    build_api_url,
    build_headers,
    create_http_client,
    get_temp_dir,
    normalize_timeout,
)
from .mimo_tts_reference import read_reference


@register_provider_adapter(
    "mimo_tts_api",
    "MiMo TTS API",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderMiMoTTSAPI(TTSProvider):
    def __init__(
        self,
        provider_config: dict,
        provider_settings: dict,
    ) -> None:
        super().__init__(provider_config, provider_settings)
        self.chosen_api_key = provider_config.get("api_key", "")
        self.api_base = provider_config.get("api_base", DEFAULT_MIMO_API_BASE)
        self.proxy = provider_config.get("proxy", "")
        self.timeout = normalize_timeout(provider_config.get("timeout", 20))
        self.voice = provider_config.get("mimo-tts-voice", DEFAULT_MIMO_TTS_VOICE)
        self.audio_format = provider_config.get("mimo-tts-format", "wav")
        self.style_prompt = provider_config.get("mimo-tts-style-prompt", "")
        self.dialect = provider_config.get("mimo-tts-dialect", "")
        self.seed_text = provider_config.get(
            "mimo-tts-seed-text", DEFAULT_MIMO_TTS_SEED_TEXT
        )
        self.reference_audio = provider_config.get("mimo-tts-reference-audio", "")
        self.set_model(provider_config.get("model", DEFAULT_MIMO_TTS_MODEL))
        self.client = create_http_client(self.timeout, self.proxy)

    def _build_user_prompt(self) -> str | None:
        # The official API uses user instructions for voice direction and
        # synthesizes only the assistant message.
        instructions = "\n".join(
            value.strip()
            for value in (self.seed_text, self.style_prompt, self.dialect)
            if value.strip()
        )
        return instructions or None

    def _build_payload(self, text: str) -> dict:
        messages: list[dict[str, str]] = []

        user_prompt = self._build_user_prompt()
        if user_prompt:
            messages.append(
                {
                    "role": "user",
                    "content": user_prompt,
                }
            )

        messages.append(
            {
                "role": "assistant",
                "content": text,
            }
        )

        audio_params = {"format": self.audio_format}
        if self.model_name == "mimo-v2.5-tts-voiceclone":
            reference = read_reference(self.reference_audio)
            audio_params["voice"] = (
                "data:audio/wav;base64," + base64.b64encode(reference).decode("ascii")
            )
        elif "voicedesign" not in self.model_name:
            audio_params["voice"] = self.voice

        return {
            "model": self.model_name,
            "messages": messages,
            "audio": audio_params,
        }

    async def get_audio(self, text: str) -> str:
        payload = await asyncio.to_thread(self._build_payload, text)
        response = await self.client.post(
            build_api_url(self.api_base),
            headers=build_headers(self.chosen_api_key),
            json=payload,
        )

        try:
            response.raise_for_status()
        except Exception as exc:
            error_text = response.text[:1024]
            raise MiMoAPIError(
                f"MiMo TTS API request failed: HTTP {response.status_code}, response: {error_text}"
            ) from exc

        data = response.json()
        choices = data.get("choices") or []
        first_choice = choices[0] if choices else {}
        message = first_choice.get("message", {})
        audio_data = message.get("audio", {}).get("data")
        if not audio_data:
            raise MiMoAPIError(f"MiMo TTS API returned no audio payload: {data}")

        output_path = (
            get_temp_dir()
            / f"mimo_tts_api_{generate_timestamp_id()}.{self.audio_format}"
        )
        output_path.write_bytes(base64.b64decode(audio_data))
        return str(output_path)

    async def terminate(self):
        if self.client:
            await self.client.aclose()
