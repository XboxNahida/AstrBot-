from types import SimpleNamespace

import pytest
from astrbot_plugin_meme_manager.mixins import web_api
from quart import Quart


@pytest.mark.asyncio
async def test_settings_suggest_robot_ids_without_exposing_platform_credentials(
    monkeypatch,
):
    monkeypatch.setattr(web_api, "get_selection_rules", lambda: {"rules": []})
    handler = object.__new__(web_api.WebAPIMixin)
    handler.context = SimpleNamespace(
        provider_manager=SimpleNamespace(personas=[]),
        get_config=lambda: {
            "platform": [
                {"id": "Nahida", "secret": "private-secret"},
                {"id": "likh", "token": "private-token"},
                {"id": "likh"},
                {"id": ""},
            ]
        },
    )
    app = Quart(__name__)
    async with app.app_context():
        response, status = await handler._api_settings_targets()
        payload = await response.get_json()
    assert status == 200
    assert payload["bot_targets"] == [
        {"id": "Nahida", "label": "Nahida"},
        {"id": "likh", "label": "likh"},
    ]
    assert "private-secret" not in str(payload)
    assert "private-token" not in str(payload)
