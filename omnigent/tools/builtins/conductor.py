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
