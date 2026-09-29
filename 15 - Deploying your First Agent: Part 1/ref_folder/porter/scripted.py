"""A stand-in model that follows a script, for tests and failure drills.

A real model is the wrong tool for testing what the *service* does around it. It is slow,
it costs something, and it will not open a refund on cue three seconds after the caller has
given up — which is exactly the moment a timeout test needs. So the service accepts a model
name of the form

    scripted:<script>            e.g.  PORTER_MODEL=scripted:refund
    scripted:<script>:<seconds>        PORTER_MODEL=scripted:refund:3   (each call sleeps 3s)

and everything else — the graph, the tools, the checkpointer, the ledger, the HTTP layer —
is the real thing. The same idea as the fake models LangChain ships for its own tests.

Scripts:
    answer   replies at once, calls nothing
    lookup   looks up order 580638, then repeats what the tool said
    refund   opens a return on 580638, then says so — whether or not anybody asked
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ORDER = "580638"


def _since_last_question(messages: list[BaseMessage]) -> list[BaseMessage]:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1:]
    return messages


def _call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}", "type": "tool_call"}


class ScriptedModel(BaseChatModel):
    script: str = "answer"
    delay_s: float = 0.0
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "porter-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls += 1
        turn = _since_last_question(messages)
        tool_results = [m for m in turn if isinstance(m, ToolMessage)]
        usage = {"input_tokens": 120, "output_tokens": 20, "total_tokens": 140}
        meta = {"model_name": f"scripted:{self.script}"}     # usage is counted per model name

        if self.script == "refund" and not tool_results:
            return AIMessage(content="", tool_calls=[_call("start_return", invoice_no=ORDER,
                                                           reason="arrived broken")],
                             usage_metadata=usage, response_metadata=meta)
        if self.script == "lookup" and not tool_results:
            return AIMessage(content="", tool_calls=[_call("look_up_order", invoice_no=ORDER)],
                             usage_metadata=usage, response_metadata=meta)
        if tool_results:
            return AIMessage(content=f"Done. {tool_results[-1].content}", usage_metadata=usage,
                             response_metadata=meta)
        return AIMessage(content=f"Order {ORDER} came to 147.01.", usage_metadata=usage,
                         response_metadata=meta)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        time.sleep(self.delay_s)
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        await asyncio.sleep(self.delay_s)          # a real await, so cancellation lands here
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])


def build(name: str) -> ScriptedModel:
    """`scripted:refund:3` → ScriptedModel(script="refund", delay_s=3)."""
    parts = name.split(":")
    script = parts[1] if len(parts) > 1 and parts[1] else "answer"
    delay = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    return ScriptedModel(script=script, delay_s=delay)
