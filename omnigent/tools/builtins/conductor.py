"""Schema-only tools for the owner-private Conductor memory provider."""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool


class SysConductorMemoryListTool(Tool):
    """List durable Markdown memory documents for the active Conductor."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_memory_list"

    @classmethod
    def description(cls) -> str:
        return "List the active Conductor's durable Markdown memory documents."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional relative path prefix to narrow the listing.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
        }


class SysConductorMemoryReadTool(Tool):
    """Read one durable Markdown memory document."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_memory_read"

    @classmethod
    def description(cls) -> str:
        return "Read one durable Markdown memory document for the active Conductor."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Canonical relative .md path, such as profile/preferences.md."
                            ),
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }


class SysConductorMemoryWriteTool(Tool):
    """Create or update one durable Markdown memory document."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_memory_write"

    @classmethod
    def description(cls) -> str:
        return (
            "Create or update one durable Markdown memory document for the active "
            "Conductor using optimistic revision checks."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Canonical relative .md path.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete replacement Markdown content.",
                        },
                        "expected_revision": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Revision returned by read/list, or 0 when creating a new path."
                            ),
                        },
                    },
                    "required": ["path", "content", "expected_revision"],
                    "additionalProperties": False,
                },
            },
        }


class SysConductorSessionUpdateTool(Tool):
    """Change an accessible session through the authenticated server API."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_session_update"

    @classmethod
    def description(cls) -> str:
        return (
            "Rename, archive, unarchive, or stop a session the user is allowed to manage. "
            "Exactly one action is performed and the server enforces the required permission."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Target session id."},
                        "action": {
                            "type": "string",
                            "enum": ["rename", "archive", "unarchive", "stop"],
                        },
                        "title": {
                            "type": "string",
                            "description": "Required only for rename.",
                        },
                    },
                    "required": ["session_id", "action"],
                    "additionalProperties": False,
                },
            },
        }


class SysConductorPermissionTool(Tool):
    """Grant, change, or revoke a session permission."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_permission"

    @classmethod
    def description(cls) -> str:
        return (
            "Grant, change, or revoke another user's access to a session. Requires manage "
            "permission on the target; public sharing remains deployment-policy bounded."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "action": {"type": "string", "enum": ["grant", "revoke"]},
                        "user_id": {
                            "type": "string",
                            "description": (
                                "User email, or __public__ when public sharing is enabled."
                            ),
                        },
                        "level": {
                            "type": "string",
                            "enum": ["read", "edit", "manage"],
                            "description": "Required for grant; defaults to read.",
                        },
                    },
                    "required": ["session_id", "action", "user_id"],
                    "additionalProperties": False,
                },
            },
        }


class SysConductorProjectTool(Tool):
    """List and manage the current user's projects."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_project"

    @classmethod
    def description(cls) -> str:
        return "List, create, rename, configure, or delete the current user's Omnigent projects."

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "create", "update", "delete"],
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Required for update and delete.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Required for create; optional for update.",
                        },
                        "config": {
                            "type": "object",
                            "description": "Optional project configuration replacement.",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }


class SysConductorSettingsTool(Tool):
    """Read or update provider-neutral Conductor settings."""

    @classmethod
    def name(cls) -> str:
        return "sys_conductor_settings"

    @classmethod
    def description(cls) -> str:
        return (
            "Read Conductor settings, or select a registered memory provider and update "
            "provider-neutral configuration."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["get", "update"]},
                        "memory_provider": {"type": "string"},
                        "config": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }
