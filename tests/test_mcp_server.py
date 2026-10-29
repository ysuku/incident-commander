"""MCP server tests: the tools the model sees come from the server, calls round-trip
through a real MCP session, and tool errors come back as notes, not crashes."""
import json
import sys
from pathlib import Path

import anyio
import pytest

from incident_commander import config, loaders, loop, tools
from incident_commander.mcp_server import DESCRIPTIONS, ToolClient, ToolError, build_server

has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")

# Listing tools touches no data, so a stand-in case is enough.
FAKE = loaders.IncidentCase(case_id="x_cpu/1", system="online-boutique", run="1",
                            inject_time=0, dir=Path("/nonexistent"),
                            root_cause_service="x", fault_type="cpu")


@pytest.fixture
def fake_client():
    with ToolClient(build_server(FAKE)) as tc:
        yield tc


def test_server_exposes_the_five_tools_with_descriptions(fake_client):
    assert fake_client.names == list(DESCRIPTIONS)
    by_name = {t.name: t for t in fake_client.tools}
    assert all(by_name[n].description == d for n, d in DESCRIPTIONS.items())
    assert by_name["runbook_lookup"].inputSchema["required"] == ["symptom"]


def test_prompt_tool_lines_are_rendered_from_schemas(fake_client):
    lines = fake_client.describe()
    assert "metric_query(service?, metric?, top?) - before/after-alert" in lines
    assert "service_map() - who calls whom" in lines
    assert "runbook_lookup(symptom, k?)" in lines          # required param: no '?'
    assert "{tools}" in loop.SYSTEM_PROMPT


def test_bad_args_come_back_as_one_line_tool_error(fake_client):
    with pytest.raises(ToolError, match="symptom") as e:
        fake_client.call("runbook_lookup", {})
    assert "\n" not in str(e.value)


@needs_data
def test_mcp_results_match_direct_calls():
    c = loaders.load_case("checkoutservice_cpu/1")
    with ToolClient(build_server(c)) as tc:
        assert tc.call("metric_query", {"top": 3}) == json.loads(json.dumps(tools.metric_query(c, top=3)))
        assert tc.call("service_map", {}) == tools.service_map(c)
        with pytest.raises(ToolError, match="available metrics"):
            tc.call("metric_query", {"metric": "p99_latency"})


class SendsListArgs:
    def complete(self, messages):
        return json.dumps({"thought": "x", "action": {"tool": "trace_lookup", "args": [5]}})


@needs_data
def test_non_object_args_are_a_note_not_a_crash():
    c = loaders.load_case("checkoutservice_cpu/1")
    brief = loop.diagnose(c, SendsListArgs(), max_steps=2)
    assert [e["outcome"] for e in brief["investigation"]] == ["bad_args", "bad_args"]


@needs_data
def test_stdio_server_serves_a_case():
    """End-to-end over a real subprocess, as an MCP host would run it."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "incident_commander.mcp_server", "--case", "checkoutservice_cpu/1"],
        cwd=str(config.REPO_ROOT), env={"PYTHONPATH": str(config.REPO_ROOT / "src")})

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            r = await s.call_tool("metric_query", {"top": 1})
            return names, json.loads(r.content[0].text)

    names, result = anyio.run(run)
    assert names == list(DESCRIPTIONS)
    assert result["top_deviations"][0]["series"] == "checkoutservice_cpu"
