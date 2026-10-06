# Does a value declared in config/config.yaml change what the compiled graph does?
#
# The manifest carries identity only. Runtime parameters live in
# config/config.yaml, which the registry loads and passes as Graph(config=...).
# A standalone entrypoint that constructs the graph bare leaves every declared
# value inert on that deployment while the registry deployment honours it — the
# same agent behaving differently depending on how it was started, with nothing
# failing anywhere to say so.
#
# Asserting that the file parses would not catch that. These tests assert that
# the value REACHES the object that reads it, and that changing the declared
# value changes the graph's behaviour.

from pathlib import Path

import pytest
from framework.schemas.agent_status import AgentStatus

from src.graph.graph import RetailEarningsCallIRSummarizationAgent
from src.services.runtime_config import CONFIG_PATH, runtime_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_config_path_points_at_the_shipped_file() -> None:
    assert CONFIG_PATH == _REPO_ROOT / "config" / "config.yaml"
    assert CONFIG_PATH.exists(), "config/config.yaml must ship with the template"


def test_declared_values_are_loaded() -> None:
    config = runtime_config()
    assert config.get("max_retry") is not None, "config/config.yaml declares no max_retry"
    assert config.get("timeout_s") is not None, "config/config.yaml declares no timeout_s"


def test_the_entrypoint_constructs_the_graph_with_the_declared_config() -> None:
    """The served agent carries the declared values, not an empty config."""
    from src.api.server import agent

    assert agent.config.get("max_retry") == runtime_config()["max_retry"]


def test_a_declared_value_changes_routing() -> None:
    """max_retry reaches the framework's own reader and changes its decision.

    The backbone routes a RETRY status back to pre_process while the retry count
    is below max_retry, and to finalize once it is not. Driving the same state
    through two graphs that differ only in their declared max_retry proves the
    declared number is the one being read — a graph constructed bare would take
    the framework default for both and return the same answer twice.
    """
    state = {"status": AgentStatus.RETRY.value, "retry_count": 1}

    generous = RetailEarningsCallIRSummarizationAgent(config={"max_retry": 5})
    exhausted = RetailEarningsCallIRSummarizationAgent(config={"max_retry": 1})

    assert generous.route(state) == "pre_process"
    assert exhausted.route(state) == "finalize"


@pytest.mark.parametrize("declared", [3, 4])
def test_declared_max_retry_is_the_ceiling_used(declared: int) -> None:
    graph = RetailEarningsCallIRSummarizationAgent(config={"max_retry": declared})
    at_ceiling = {"status": AgentStatus.RETRY.value, "retry_count": declared}
    below_ceiling = {"status": AgentStatus.RETRY.value, "retry_count": declared - 1}
    assert graph.route(at_ceiling) == "finalize"
    assert graph.route(below_ceiling) == "pre_process"
