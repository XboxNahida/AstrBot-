import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest


class TaskFinalVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_intermediate_text_and_reasoning_are_not_sent(self):
        event = MagicMock()
        event.is_stopped.return_value = False
        event.get_extra.return_value = None
        event.get_platform_name.return_value = "test"

        class Runner:
            streaming = False
            req = ProviderRequest(final_response_only=True)
            run_context = SimpleNamespace(context=SimpleNamespace(event=event))
            finished = False

            async def step(self):
                yield AgentResponse(
                    type="llm_result",
                    data={"chain": MessageChain().message("GPT 准备调用工具")},
                )
                self.finished = True
                yield AgentResponse(
                    type="llm_result",
                    data={"chain": MessageChain(type="reasoning").message("内部推理")},
                )
                yield AgentResponse(
                    type="llm_result",
                    data={"chain": MessageChain().message("小七最终回复")},
                )

            def done(self):
                return self.finished

        chains = [
            chain
            async for chain in run_agent(
                Runner(), buffer_intermediate_messages=True, show_reasoning=True
            )
        ]
        self.assertEqual(["小七最终回复"], [chain.get_plain_text() for chain in chains])
