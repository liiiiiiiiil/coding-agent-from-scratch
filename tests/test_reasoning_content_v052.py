"""Provider extension round trips using an offline HTTP transport double."""

import json

import pytest

from mini_agent.agent import ParentRuntimePolicy
from mini_agent.context import ContextBudget, ContextManager, TrimPolicy
from mini_agent.providers.base import ProviderProtocolError, ProviderStreamError
from mini_agent.providers.catalog import ProviderCatalog
from mini_agent.runtime import AgentRuntime
from mini_agent.session import SessionStore
from mini_agent.state import AgentState
from mini_agent.tools.base import Tool, ToolExecutor, ToolRegistry


def _binding():
    return ProviderCatalog.from_settings(
        {"fixture": {"protocol": "openai_chat", "endpoint": "https://fixture.invalid/chat/completions", "api_key": "fixture-key"}},
        {"fixture": {"provider_id": "fixture", "model_id": "fixture-model", "context_window": 128000, "max_output_tokens": 256}},
        parent_profile="fixture", subagent_allowed_profiles=("fixture",),
    ).parent_binding()


def _payload(message):
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def test_runtime_preserves_reasoning_through_tool_result_trim_and_session(tmp_path, monkeypatch):
    binding = _binding()
    requests = []
    class Transport:
        def json(self, body, **_):
            request = json.loads(body)
            requests.append(request)
            if len(requests) == 1:
                return _payload({"content": None, "reasoning_content": "opaque protocol text", "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "calculate", "arguments": '{"expression":"1+1"}'},
                }]})
            assistant = next(m for m in request["messages"] if m["role"] == "assistant")
            assert assistant["reasoning_content"] == "opaque protocol text"
            assert any(m.get("tool_call_id") == "call-1" for m in request["messages"])
            return _payload({"content": "done", "reasoning_content": "closing protocol text"})
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    state = AgentState()
    state.begin_task("calculate")
    registry = ToolRegistry()
    registry.register(Tool("calculate", "fixture", {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}, lambda expression: "2", effect_class="none"))
    context = ContextManager(state, [{"role": "user", "content": "calculate"}], memory_retrieval_enabled=False)
    class NonStreamingPolicy(ParentRuntimePolicy):
        def llm_options(self, runtime):
            return dict(super().llm_options(runtime), stream_output=False)
    runtime = AgentRuntime(llm_client=binding.complete, context=context,
                           executor=ToolExecutor(registry, on_result=state.record_tool),
                           policy=NonStreamingPolicy(), model_binding=binding, max_rounds=3)
    assert runtime.run().stop_reason == "text"
    assert context.history[-1]["reasoning_content"] == "closing protocol text"
    assert "opaque protocol text" not in json.dumps(state.snapshot())
    prepared = TrimPolicy().trim(context.history, ContextBudget(window=128000))
    assert next(m for m in prepared if m["role"] == "assistant")["reasoning_content"] == "opaque protocol text"
    store = SessionStore(tmp_path / "sessions")
    saved = store.save(None, state, context, workspace_root=tmp_path)
    snapshot = store.load(saved["session_id"])
    assert snapshot["context"]["history"][-1]["reasoning_content"] == "closing protocol text"


@pytest.mark.parametrize("reasoning", [None, "", "protocol text"])
def test_nonstream_nullable_reasoning_is_preserved(monkeypatch, reasoning):
    binding = _binding()
    class Transport:
        def json(self, *_args, **_options):
            return _payload({"content": "answer", "reasoning_content": reasoning})
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    assert binding.complete([]).message["reasoning_content"] == reasoning


def test_ordinary_response_does_not_invent_reasoning(monkeypatch):
    binding = _binding()
    class Transport:
        def json(self, *_args, **_options):
            return _payload({"content": "answer"})
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    assert "reasoning_content" not in binding.complete([]).message


def test_stream_assembles_reasoning_without_printing_and_passes_it_back(monkeypatch):
    binding = _binding()
    visible = []
    requests = []
    class Transport:
        def stream(self, body, **_):
            requests.append(json.loads(body))
            for delta in ({"reasoning_content": "opaque "}, {"reasoning_content": "text"}, {"content": "answer"}):
                yield json.dumps({"choices": [{"delta": delta}]})
            yield "[DONE]"
        def json(self, body, **_):
            requests.append(json.loads(body))
            assert requests[-1]["messages"][0]["reasoning_content"] == "opaque text"
            return _payload({"content": "next"})
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    first = binding.complete([], stream_output=True, on_content=visible.append)
    assert first.message["reasoning_content"] == "opaque text"
    assert visible == ["answer"]
    binding.complete([first.message])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("value", [123, "too long"])
def test_invalid_or_oversize_reasoning_rejected_without_body_in_error(monkeypatch, stream, value):
    binding = _binding()
    monkeypatch.setattr("mini_agent.providers.openai_chat.MAX_CONTENT_CHARS", 3)
    class Transport:
        def json(self, *_args, **_options):
            return _payload({"content": "ok", "reasoning_content": value})
        def stream(self, *_args, **_options):
            yield json.dumps({"choices": [{"delta": {"reasoning_content": value}}]})
            yield "[DONE]"
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    with pytest.raises(ProviderStreamError if stream else ProviderProtocolError) as error:
        binding.complete([], stream_output=stream)
    assert str(value) not in str(error.value)


@pytest.mark.parametrize("stream,finish,infra", [
    (False, "tool_calls", None), (False, "length", None),
    (True, "length", None), (True, "tool_calls", "provider_protocol_error"),
    (False, None, "provider_protocol_error"),
])
def test_invalid_tool_json_has_safe_diagnostics_and_truthful_usage(monkeypatch, stream, finish, infra):
    from mini_agent.providers.base import ProviderToolArgumentsError
    from mini_agent.evaluation.reliability_worker import _exception_diagnostic, _provider_infrastructure_category
    binding = _binding()
    private = '{"private":"SECRET_ARGUMENT_BODY",'
    call = {"id": "fixture", "type": "function", "function": {"name": "read_file", "arguments": private}}
    usage = {"prompt_tokens": 5000, "completion_tokens": 256}
    class Transport:
        def json(self, *_args, **_options):
            return {"choices": [{"message": {"content": None, "tool_calls": [call]},
                                 "finish_reason": finish}], "usage": usage}
        def stream(self, *_args, **_options):
            yield json.dumps({"choices": [{"delta": {"tool_calls": [dict(call, index=0)]},
                                          "finish_reason": finish}], "usage": usage})
            yield "[DONE]"
    monkeypatch.setattr(binding.adapter, "_client", lambda _: Transport())
    with pytest.raises(ProviderToolArgumentsError) as caught:
        binding.complete([], stream_output=stream)
    error = caught.value
    diagnostic = _exception_diagnostic(error, stage="agent", binding=binding)
    assert "SECRET_ARGUMENT_BODY" not in json.dumps(diagnostic)
    assert diagnostic["tool_arguments"]["argument_chars"] == len(private)
    assert diagnostic["tool_arguments"]["json_position"] is not None
    assert diagnostic["tool_arguments"]["finish_reason"] == (finish or "unknown")
    assert diagnostic["tool_arguments"]["reported_output_tokens"] == 256
    assert _provider_infrastructure_category(error) == infra
    meter = binding.usage_meter.snapshot()
    assert meter["input_tokens"] == 5000 and meter["output_tokens"] == 256
    assert meter["token_accounting"] == "provider"


def test_non_object_tool_arguments_are_model_failure_without_execution():
    from mini_agent.providers.openai_chat import _message_from_payload
    from mini_agent.providers.base import ProviderToolArgumentsError
    from mini_agent.evaluation.reliability_worker import _provider_infrastructure_category
    with pytest.raises(ProviderToolArgumentsError) as caught:
        _message_from_payload({"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "fixture", "type": "function", "function": {
                "name": "read_file", "arguments": "[]"}}]}}]})
    assert caught.value.diagnostic["code"] == "not_object"
    assert _provider_infrastructure_category(caught.value) is None
