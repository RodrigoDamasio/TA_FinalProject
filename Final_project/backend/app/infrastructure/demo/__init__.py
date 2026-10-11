"""LLM_MODE=fake: deterministic agents, no key, no quota (Shared router). Used for UI work,
local E2E and as the base of the test fake. It reads the same prompts Gemini would get, so
the whole loop (protocol, tool calls, citations, report, approval) runs as in production.

Each agent's script lives in its own module, owned by that agent's plan (v1.1 §24)."""

import json
import time

from app.domain.ports import LLMRequest, LLMResponse, TokenUsage, UserMessage

from . import researcher, reviewer, supervisor, writer


class DemoLLMClient:
    def __init__(self, delay_s: float = 0.0) -> None:
        self._delay = delay_s  # makes the live activity feed visible in E2E

    def generate(self, request: LLMRequest) -> LLMResponse:
        if self._delay:
            time.sleep(self._delay)
        prompt = "\n".join(m.text for m in request.messages if isinstance(m, UserMessage))
        schema = request.response_schema.__name__ if request.response_schema else None
        usage = TokenUsage(input=len(request.system + prompt) // 4, output=80)
        if schema is not None:
            data = reviewer.review() if schema == "ReviewLLM" else reviewer.fact_check(prompt)
            return LLMResponse(json.dumps(data), [], data, usage, "stop")
        calls = []
        if "You are the Supervisor" in request.system:
            text: str | None = supervisor.respond(prompt)
        elif "You are the Researcher" in request.system:
            text, calls = researcher.respond(request, prompt)
        elif "You are the Writer" in request.system:
            text = writer.respond(prompt)
        else:
            text = "Demo mode."
        return LLMResponse(text, calls, None, usage, "stop")
