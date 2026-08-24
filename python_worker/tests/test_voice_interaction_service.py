"""Tests for voice navigation request creation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from application.dtos.route_dtos import VoiceCommandRequest
from application.voice_interaction_service import VoiceInteractionService


class FakeVoiceClient:
    def __init__(self, payload):
        self.payload = payload

    def parse_navigation_intent(self, _text):
        return self.payload


class TestVoiceInteractionService:
    def test_create_navigation_request_from_places(self):
        service = VoiceInteractionService()
        result = service.create_navigation_request("枣庄学院", "万达广场")

        assert result["need_planning"] is True
        assert result["navigation_request"] is not None
        assert result["origin_desc"] == "枣庄学院"
        assert result["destination_desc"] == "万达广场"

    def test_create_navigation_request_rejects_unknown_destination(self):
        service = VoiceInteractionService()
        result = service.create_navigation_request("枣庄学院", "不存在的地方")

        assert result["need_planning"] is False
        assert "不存在的地方" in result["response_text"]

    def test_process_command_uses_fake_navigation_parser(self):
        client = FakeVoiceClient(
            {
                "intent": "navigation",
                "origin": "枣庄学院",
                "destination": "万达广场",
                "preferences": {"avoid_stairs": True},
            }
        )
        service = VoiceInteractionService(voice_client=client)
        result = service.process_command(VoiceCommandRequest(text="去万达广场"))

        assert result["need_planning"] is True
        assert result["navigation_request"] is not None
        assert result["navigation_request"].preferences["avoid_stairs"] is True

    def test_process_command_returns_chat_response_for_non_navigation(self):
        client = FakeVoiceClient({"intent": "query"})
        service = VoiceInteractionService(voice_client=client)
        result = service.process_command(VoiceCommandRequest(text="你能做什么"))

        assert result["need_planning"] is False
        assert result["navigation_request"] is None
        assert "路线" in result["response_text"]
