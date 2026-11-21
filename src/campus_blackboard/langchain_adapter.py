"""LangChain chat model and prompt chain over the configured provider adapters."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from pydantic import Field


class CampusChatModel(BaseChatModel):
    """Preserve provider isolation, cancellation and measured usage in LangChain."""

    backend: Any = Field(exclude=True, repr=False)

    @property
    def _llm_type(self) -> str:
        return "campus-configured-provider"

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        if stop:
            raise ValueError("stop sequences are not supported by the campus provider contract")
        if len(messages) != 2 or not isinstance(messages[0], SystemMessage) or not isinstance(messages[1], HumanMessage):
            raise ValueError("campus model requires a system prompt and a JSON task message")
        request = json.loads(messages[1].content)
        raw = await self.backend.complete(
            role=request["role"], purpose=request["purpose"], instructions=messages[0].content,
            context=request["context"], output_schema=request["output_schema"],
        )
        if not isinstance(raw, str):
            raise TypeError("model adapter returned non-text output")
        metadata = getattr(self.backend, "last_call_metadata", None)
        message = AIMessage(content=raw, response_metadata=dict(metadata) if isinstance(metadata, dict) else {})
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        # The application uses ainvoke so cancellation reaches provider subprocesses.
        return asyncio.run(self._agenerate(messages, stop=stop, **kwargs))


def specialist_chain(backend: Any):
    """Prompt → configured chat model → text, followed by domain validation."""
    prompt = ChatPromptTemplate.from_messages([("system", "{instructions}"), ("human", "{task}")])
    return prompt | CampusChatModel(backend=backend) | StrOutputParser()
