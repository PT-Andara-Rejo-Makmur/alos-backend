"""Canonical backend permission names used by implementation policies."""

from alos.permissions.registry import PermissionRegistry, RoleGrant

TOOL_DIAGNOSTIC_EXECUTE = "tools.diagnostic.execute"

__all__ = ["TOOL_DIAGNOSTIC_EXECUTE", "PermissionRegistry", "RoleGrant"]
