from types import SimpleNamespace

from errand.model import AnthropicModel, ScriptedModel


def test_scripted_model_runs_offline():
    model = ScriptedModel([{"type": "tool", "name": "search_providers", "input": {"service": "plumber"}}])
    assert model.next([{"role": "user", "content": "Find a plumber"}], []) == {
        "type": "tool", "name": "search_providers", "input": {"service": "plumber"}
    }


def test_scripted_model_can_use_previous_tool_result():
    model = ScriptedModel([lambda messages, tools: {"type": "text", "text": messages[-1]["content"]}])
    assert model.next([{"role": "user", "content": "result"}], [])["text"] == "result"


def test_real_model_adapter_uses_client_tool_call_and_bounded_output():
    recorded = {}

    class Messages:
        def create(self, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", id="tool-1", name="search_providers", input={"service": "plumber"})],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

    client = SimpleNamespace(messages=Messages())
    model = AnthropicModel(client=client)
    result = model.next([{"role": "user", "content": "Find a plumber"}], [{"name": "search_providers"}])

    assert result["name"] == "search_providers"
    assert result["input"] == {"service": "plumber"}
    assert recorded["model"] == "claude-sonnet-5-5"
    assert recorded["max_tokens"] <= 512
    assert recorded["tool_choice"]["disable_parallel_tool_use"] is True
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert result["tool_use_id"] == "tool-1"
