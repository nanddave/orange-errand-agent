"""Live tool-use model and deterministic offline replacement."""

import copy
import json
import os
import tempfile
from pathlib import Path

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
        self.client = client or Anthropic(max_retries=1, timeout=20.0)

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


class RecordingModel:
    """Wraps a model and saves only its step dicts (never keys or headers) to a JSON cassette."""

    def __init__(self, inner, path):
        self.inner, self.path, self.steps = inner, Path(path), []
        self.live = getattr(inner, "live", False)

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        step = self.inner.next(messages, tools)
        self.steps.append(json.loads(json.dumps(step, default=str)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            json.dump(self.steps, handle, indent=2)
        os.replace(tmp, self.path)
        return step


def _latest_hold_id(messages):
    content = messages[-1]["content"] if messages else ""
    if isinstance(content, list):
        content = next((b.get("content") for b in content if b.get("type") == "tool_result"), "")
    try:
        return json.loads(content).get("hold_id")
    except (TypeError, ValueError, AttributeError):
        return None


class ReplayModel:
    """Plays a recorded cassette back offline; `book` gets the current run's hold id."""
    live = False

    def __init__(self, path):
        self.steps = iter(json.loads(Path(path).read_text()))

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        step = next(self.steps, None)
        if step is None:
            raise RuntimeError("cassette exhausted: the replayed conversation has no more recorded steps")
        step = copy.deepcopy(step)
        hold_id = _latest_hold_id(messages) if step.get("name") == "book" else None
        if hold_id:
            step["input"]["hold_id"] = hold_id
            for block in step.get("assistant_content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "book":
                    block["input"]["hold_id"] = hold_id
        return step
