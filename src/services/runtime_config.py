"""AgentCore Platform v1.0 — RET-C2-303 runtime configuration loader"""

# Single reader for config/config.yaml.
#
# config/agent.yaml is the static manifest: identity, entry point, trust level
# and the compile-time `requires` gates. It carries no runtime parameters, so a
# reader that looks there for `max_retry` or `timeout_s` finds nothing and
# degrades to a framework default with nothing failing anywhere.
#
# Runtime parameters live in config/config.yaml. The registry loads that file
# itself and passes it as Graph(config=...); the standalone entry point has to
# do the same, or every declared value is inert on that deployment.
#
# tests/integration/test_runtime_config_reaches_the_graph.py proves a declared
# value changes what the compiled graph uses, rather than asserting the file
# merely parses.

from __future__ import annotations

from pathlib import Path
from typing import Any

from framework.utils.config_loader import load_config

# src/services/<this file> -> parents[2] is the repository root.
CONFIG_PATH: Path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def runtime_config(path: Path | None = None) -> dict[str, Any]:
    """Return the declared runtime parameters, or {} when the file is absent.

    The absent-file tolerance mirrors the registry's own `if exists() else {}`
    guard, so the two deployment paths behave identically rather than one of
    them crashing on a repository that ships no runtime configuration.
    """
    target = path or CONFIG_PATH
    if not target.exists():
        return {}
    loaded = load_config(str(target))
    return dict(loaded) if isinstance(loaded, dict) else {}
