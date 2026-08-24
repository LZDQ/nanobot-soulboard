"""Soulboard-specific MCP wrappers."""

from loguru import logger
from nanobot.agent.tools import mcp as upstream_mcp


class SoulMCPWrapperBase(upstream_mcp._MCPWrapperBase):
    """Reconnect dead MCP transports before retrying a capability call."""

    async def _refresh_session_after_termination(
        self,
        exc: BaseException,
        already_refreshed: bool,
        capability_kind: str,
    ) -> bool:
        should_reconnect = (
            upstream_mcp._is_session_terminated(exc)
            or upstream_mcp._is_transient(exc)
        )
        if already_refreshed or not should_reconnect or self._reconnect is None:
            return False

        logger.warning(
            "MCP {} '{}' transport closed ({}); reconnecting server '{}' before retry",
            capability_kind,
            self._name,
            type(exc).__name__,
            self._server_name,
        )
        refreshed_tool = await self._reconnect(self._server_name, self._name, self)
        if not isinstance(refreshed_tool, upstream_mcp._MCPWrapperBase):
            logger.warning(
                "MCP {} '{}' could not refresh session for server '{}'",
                capability_kind,
                self._name,
                self._server_name,
            )
            return False
        self._session = refreshed_tool._session
        return True


class SoulMCPToolWrapper(SoulMCPWrapperBase, upstream_mcp.MCPToolWrapper):
    """MCP tool wrapper with Soulboard reconnect behavior."""


class SoulMCPResourceWrapper(SoulMCPWrapperBase, upstream_mcp.MCPResourceWrapper):
    """MCP resource wrapper with Soulboard reconnect behavior."""


class SoulMCPPromptWrapper(SoulMCPWrapperBase, upstream_mcp.MCPPromptWrapper):
    """MCP prompt wrapper with Soulboard reconnect behavior."""
