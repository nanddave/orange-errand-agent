"""Live tool-use model and deterministic offline replacement."""

from anthropic import Anthropic


class ScriptedModel:
    def __init__(self, steps: list[dict]):
        self.steps = iter(steps)

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        step = next(self.steps)
        return step(messages, tools) if callable(step) else step


class AnthropicModel:
    live = True

    def __init__(self, client=None):
        self.client = client or Anthropic(max_retries=0, timeout=20.0)

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        response = self.client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=512,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            tools=tools,
            messages=messages,
        )
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        tool_call = next((block for block in response.content if block.type == "tool_use"), None)
        if tool_call:
            return {
                "type": "tool", "name": tool_call.name, "input": tool_call.input,
                "tool_use_id": tool_call.id,
                "assistant_content": [block.model_dump() if hasattr(block, "model_dump") else vars(block) for block in response.content],
                "usage": usage,
            }
        return {
            "type": "text",
            "text": " ".join(block.text for block in response.content if block.type == "text"),
            "usage": usage,
        }
