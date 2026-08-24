"""Soulboard-specific MCP wrappers."""

from loguru import logger
from nanobot.agent.tools import mcp as upstream_mcp


DEFAULT_MCP_RECONNECT_TIMEOUT_SECONDS = 30.0


class SoulMCPReconnectError(RuntimeError):
    """Raised when a dead MCP session could not be replaced safely."""


def _requires_fresh_session(exc: BaseException) -> bool:
    """Return whether retrying the existing MCP session cannot recover."""
    return (
        upstream_mcp._is_session_terminated(exc)
        or upstream_mcp._is_transient(exc)
    )


class SoulMCPWrapperBase(upstream_mcp._MCPWrapperBase):
    """Reconnect dead MCP transports before retrying a capability call."""

    async def _refresh_session_after_termination(
        self,
        exc: BaseException,
        already_refreshed: bool,
        capability_kind: str,
    ) -> bool:
        if not _requires_fresh_session(exc) or self._reconnect is None:
            return False
        if already_refreshed:
            raise SoulMCPReconnectError(
                f"MCP {capability_kind} '{self._name}' failed on a fresh session "
                f"with {type(exc).__name__}"
            )

        logger.warning(
            "MCP {} '{}' transport closed ({}); reconnecting server '{}' before retry",
            capability_kind,
            self._name,
            type(exc).__name__,
            self._server_name,
        )
        refreshed_tool = await self._reconnect(self._server_name, self._name, self)
        if not isinstance(refreshed_tool, upstream_mcp._MCPWrapperBase):
            message = (
                f"MCP {capability_kind} '{self._name}' could not refresh "
                f"server '{self._server_name}'"
            )
            logger.warning("{}", message)
            raise SoulMCPReconnectError(message)
        self._session = refreshed_tool._session
        return True


class SoulMCPToolWrapper(SoulMCPWrapperBase, upstream_mcp.MCPToolWrapper):
    """MCP tool wrapper with Soulboard reconnect behavior."""


class SoulMCPResourceWrapper(SoulMCPWrapperBase, upstream_mcp.MCPResourceWrapper):
    """MCP resource wrapper with Soulboard reconnect behavior."""


class SoulMCPPromptWrapper(SoulMCPWrapperBase, upstream_mcp.MCPPromptWrapper):
    """MCP prompt wrapper with Soulboard reconnect behavior."""
