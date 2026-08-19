"""Built-in tool schema for Coder-backed Omni host discovery."""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool, ToolContext


class SysCoderHostsTool(Tool):
    """Rank connected Coder workspaces for an Omni child session."""

    @classmethod
    def name(cls) -> str:
        """:returns: ``"sys_coder_hosts"``."""
        return "sys_coder_hosts"

    @classmethod
    def description(cls) -> str:
        """:returns: Human-readable description of the tool."""
        return (
            "List and rank Coder workspaces that have an online Omnigent host. "
            "Reads advisory memory, CPU, load, and container observations from "
            "Coder metadata with a bounded Coder SSH fallback; Omnigent does not "
            "collect its own resource telemetry. Memory determines the placement "
            "recommendation. CPU, logical CPU count, load, and container count "
            "only rank hosts and never cap coding-agent sessions. Returns the "
            "Omnigent host_id and verified repository workspace path needed by "
            "sys_session_create. If needs_confirmation is true, ask the human "
            "before dispatching to an over-capacity or unmeasured host."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format discovery-tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_key": {
                            "type": "string",
                            "description": (
                                "Coder agent-metadata key for used/total memory. "
                                "Defaults to 'mem'; common aliases are also tried."
                            ),
                        },
                        "cpu_key": {
                            "type": "string",
                            "description": (
                                "Coder agent-metadata key for CPU percentage. "
                                "Defaults to 'cpu'. Ranking only."
                            ),
                        },
                        "load_key": {
                            "type": "string",
                            "description": (
                                "Coder agent-metadata key for one-minute load. "
                                "Defaults to 'load'. Ranking only."
                            ),
                        },
                        "containers_key": {
                            "type": "string",
                            "description": (
                                "Coder agent-metadata key for active containers. "
                                "Defaults to 'stack_containers'. Ranking only."
                            ),
                        },
                        "max_age_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 3600,
                            "description": (
                                "Maximum preferred metadata age. The actual bound "
                                "is at least 30 seconds and three reporting intervals."
                            ),
                        },
                        "requested_memory_gib": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": 1024,
                            "description": (
                                "Advisory memory requested for the new coding-agent "
                                "session. Defaults to 4 GiB."
                            ),
                        },
                        "memory_reserve_gib": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1024,
                            "description": (
                                "Advisory memory to leave free after placement. Defaults to 1 GiB."
                            ),
                        },
                        "ssh_fallback": {
                            "type": "boolean",
                            "description": (
                                "Use a bounded, fixed Coder SSH probe for missing "
                                "metrics and repository-path verification. Defaults true."
                            ),
                        },
                        "repository_remote": {
                            "type": "string",
                            "description": (
                                "Optional origin URL for the caller's repository. "
                                "When supplied, only Coder workspaces whose probed "
                                "origin matches it are eligible. Credentials are "
                                "stripped before comparison or output."
                            ),
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        """Raise if runner-side interception was unexpectedly bypassed."""
        del arguments, ctx
        raise RuntimeError(
            "sys_coder_hosts is handled by runner tool dispatch; "
            "this invoke() path should never be reached"
        )
