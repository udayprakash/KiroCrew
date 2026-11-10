"""A remote MCP server's ``oauth`` block on the KAS projection.

KAS engines before 0.66.104 drop the block a ``customAgents`` entry carries, so a
pre-registered client cannot sign in there; 0.66.104 keeps it. :mod:`kiro_crew.acp.kas_agents`
sends it to every engine (``kas_engine_features.carry_oauth``), and the session-start
notice names the loss only where Crew knows the engine is older. This leaf holds
the pieces that notice reads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from kiro_crew.mcp_cleanup import (
    KIROCREW_BIN_MCP_SERVERS,
    mcp_entry_is_muted,
    mcp_entry_is_registry_governed,
)

#: How a server's discarded ``oauth`` block is named in the lost-keys list, and
#: how the session notice reads the server name back out of it.
MCP_OAUTH_KEY_PREFIX = "mcpServers."
MCP_OAUTH_KEY_SUFFIX = ".oauth"

_MANAGED = frozenset(KIROCREW_BIN_MCP_SERVERS)


def registry_withheld(name: str, entry: dict[str, Any], registry_mode: bool) -> bool:
    """Whether the KAS projection withholds *entry* for the registry filter."""
    return name not in _MANAGED and (registry_mode or mcp_entry_is_registry_governed(entry))


def servers_with_discarded_oauth(
    spec: dict[str, Any], registry_governed: Callable[[], bool]
) -> list[str]:
    """Names of *spec*'s declared MCP servers whose ``oauth`` block KAS discards.

    An entry the projection never declares (muted, or withheld by the registry
    filter) is skipped: it does not run at all, so naming its sign-in would
    mislead. *registry_governed* is read only when some entry sets ``oauth``.
    """
    servers = spec.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    with_oauth = [
        (name, entry)
        for name, entry in servers.items()
        if isinstance(name, str)
        and name
        and isinstance(entry, dict)
        and entry.get("oauth")
        and not mcp_entry_is_muted(entry)
    ]
    if not with_oauth:
        return []
    mode = registry_governed()
    return [name for name, entry in with_oauth if not registry_withheld(name, entry, mode)]


def oauth_lost_keys(names: Iterable[str]) -> list[str]:
    """The lost-keys entries for *names*: ``mcpServers.<name>.oauth``."""
    return [f"{MCP_OAUTH_KEY_PREFIX}{name}{MCP_OAUTH_KEY_SUFFIX}" for name in names]


def oauth_servers_in(keys: Iterable[str]) -> list[str]:
    """The server names read back out of the ``mcpServers.<name>.oauth`` *keys*."""
    pre, suf = MCP_OAUTH_KEY_PREFIX, MCP_OAUTH_KEY_SUFFIX
    return [
        k[len(pre) : -len(suf)]
        for k in keys
        if k.startswith(pre) and k.endswith(suf) and len(k) > len(pre) + len(suf)
    ]
