"""Coder-backed discovery for skill-driven Omni session placement.

Coder remains the source of CPU, memory, load, and container observations.
Omnigent stores only the immutable workspace identity a connected host reports,
then intersects those hosts with Coder's live workspace API at dispatch time.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from omnigent.json_types import JsonObject

DEFAULT_MEMORY_KEY = "mem"
DEFAULT_CPU_KEY = "cpu"
DEFAULT_LOAD_KEY = "load"
DEFAULT_CONTAINERS_KEY = "stack_containers"
DEFAULT_MAX_AGE_SECONDS = 90
DEFAULT_REQUESTED_MEMORY_GIB = 4.0
DEFAULT_MEMORY_RESERVE_GIB = 1.0

_API_TIMEOUT_SECONDS = 10.0
_SSH_TIMEOUT_SECONDS = 12.0
_MEMORY_ALIASES = ("memory", "ram", "memory_usage")
_CPU_ALIASES = ("cpu_usage", "processor")
_LOAD_ALIASES = ("load_average", "load_1m")
_CONTAINER_ALIASES = ("containers", "container_count", "docker_containers")
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$", re.IGNORECASE)
_MEMORY_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b)?\s*/\s*"
    r"(\d+(?:\.\d+)?)\s*([kmgt]?i?b)?\s*$",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# Fixed, input-free remote probe. Values are tab-separated so the local parser
# never evaluates remote output. CPU count is descriptive only; load normalized
# by it participates in ranking but it never limits the number of sessions.
_SSH_PROBE = r"""
mem_available_kib=$(grep '^MemAvailable:' /proc/meminfo 2>/dev/null | tr -s ' ' | cut -d ' ' -f 2)
physical_pages=$(getconf _PHYS_PAGES 2>/dev/null || true)
page_size=$(getconf PAGESIZE 2>/dev/null || true)
load_1m=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
containers=""
if command -v docker >/dev/null 2>&1; then
  containers=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')
elif command -v podman >/dev/null 2>&1; then
  containers=$(podman ps -q 2>/dev/null | wc -l | tr -d ' ')
fi
printf 'stats\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$mem_available_kib" "$physical_pages" "$page_size" \
  "$load_1m" "$cpu_count" "$containers"
find "$HOME" -maxdepth 3 -type d -name .git -print 2>/dev/null |
while IFS= read -r git_dir; do
  repo_root=${git_dir%/.git}
  repo_remote=$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)
  if [ -n "$repo_remote" ]; then
    printf 'repo\t%s\t%s\n' "$repo_root" "$repo_remote"
  fi
done
""".strip()


def _json_object(value: object) -> JsonObject | None:
    """Return a string-keyed JSON object when *value* has that shape."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(JsonObject, value)


def _objects(value: object) -> list[JsonObject]:
    """Return object entries from a JSON list, dropping malformed entries."""
    if not isinstance(value, list):
        return []
    return [parsed for item in value if (parsed := _json_object(item)) is not None]


def _normalized_key(value: object) -> str:
    """Normalize a metadata key or display name for alias matching."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _duration_seconds(value: object) -> float | None:
    """Parse Coder metadata age/interval values into seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        # Some SDK surfaces serialize Go durations as nanoseconds, while the
        # REST API normally returns seconds. Values this large cannot be a
        # useful metadata age in seconds, so normalize them defensively.
        return numeric / 1_000_000_000 if numeric > 10_000_000 else numeric
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.match(value)
    if match is None:
        return None
    numeric = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return numeric * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]


def _metadata_entries(agent: JsonObject) -> dict[str, JsonObject]:
    """Index Coder's template-defined agent metadata by normalized names."""
    raw = agent.get("metadata")
    items = _objects(raw)
    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            parsed = _json_object(value)
            if parsed is None:
                continue
            item = dict(parsed)
            item.setdefault("key", key)
            items.append(item)

    indexed: dict[str, JsonObject] = {}
    for item in items:
        description = _json_object(item.get("description")) or {}
        for key in (
            item.get("key"),
            description.get("key"),
            description.get("display_name"),
        ):
            normalized = _normalized_key(key)
            if normalized:
                indexed.setdefault(normalized, item)
    return indexed


def _fresh_metadata_value(
    metadata: Mapping[str, JsonObject],
    names: Sequence[str],
    max_age_seconds: int,
) -> str | None:
    """Return the first non-error, fresh metadata value for *names*."""
    for name in names:
        item = metadata.get(_normalized_key(name))
        if item is None:
            continue
        result = _json_object(item.get("result")) or item
        error = result.get("error")
        value = result.get("value")
        if error not in (None, "") or not isinstance(value, str) or not value.strip():
            continue
        description = _json_object(item.get("description")) or {}
        interval = _duration_seconds(description.get("interval")) or 0.0
        allowed_age = max(float(max_age_seconds), 30.0, interval * 3.0)
        age = _duration_seconds(result.get("age"))
        if age is None or age > allowed_age:
            continue
        return value.strip()
    return None


def _memory_bytes(number: str, unit: str | None) -> float:
    """Convert a parsed memory number and unit to bytes."""
    normalized = (unit or "b").lower()
    powers = {
        "b": 0,
        "kb": 1,
        "kib": 1,
        "mb": 2,
        "mib": 2,
        "gb": 3,
        "gib": 3,
        "tb": 4,
        "tib": 4,
    }
    base = 1024.0 if "i" in normalized else 1000.0
    return float(number) * (base ** powers.get(normalized, 0))


def _parse_memory(value: str | None) -> tuple[float | None, float | None]:
    """Parse dashboard values such as ``9.0Gi/30Gi`` into bytes."""
    if value is None or (match := _MEMORY_RE.match(value)) is None:
        return None, None
    return (
        _memory_bytes(match.group(1), match.group(2)),
        _memory_bytes(match.group(3), match.group(4)),
    )


def _parse_percent(value: str | None) -> float | None:
    """Parse a percentage value, returning its numeric percent."""
    if value is None or (match := _PERCENT_RE.search(value)) is None:
        return None
    return float(match.group(1))


def _parse_number(value: str | None) -> float | None:
    """Parse the first numeric token from a metadata value."""
    if value is None or (match := _NUMBER_RE.search(value)) is None:
        return None
    return float(match.group(0))


def _normalize_git_remote(value: object) -> str | None:
    """Normalize a git remote for credential-free repository comparison."""
    if not isinstance(value, str) or not value.strip():
        return None
    remote = value.strip()
    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.hostname:
            path = parsed.path.strip("/")
            normalized = f"{parsed.hostname.lower()}/{path}"
        elif parsed.scheme == "file":
            normalized = parsed.path
        else:
            return None
    else:
        ssh_match = re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", remote)
        if ssh_match is not None:
            normalized = f"{ssh_match.group(1).lower()}/{ssh_match.group(2).lstrip('/')}"
        else:
            normalized = remote
    normalized = normalized.rstrip("/")
    return normalized[:-4] if normalized.endswith(".git") else normalized


def _coder_token() -> str | None:
    """Resolve the Coder token without logging or placing it in argv."""
    token = os.environ.get("CODER_SESSION_TOKEN")
    if token:
        return token.strip()
    if shutil.which("coder") is None:
        return None
    try:
        result = subprocess.run(
            ["coder", "login", "token"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _coder_url() -> str | None:
    """Resolve the Coder server URL from the environment or CLI config."""
    configured = (os.environ.get("CODER_URL") or "").strip().rstrip("/")
    if configured:
        return configured

    config_paths: list[Path] = []
    explicit_config_dir = (os.environ.get("CODER_CONFIG_DIR") or "").strip()
    if explicit_config_dir:
        config_paths.append(Path(explicit_config_dir).expanduser() / "url")
    xdg_config_home = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        config_paths.append(Path(xdg_config_home).expanduser() / "coderv2" / "url")
    home = Path.home()
    config_paths.extend(
        (
            home / ".config" / "coderv2" / "url",
            home / "Library" / "Application Support" / "coderv2" / "url",
        )
    )
    for path in dict.fromkeys(config_paths):
        try:
            value = path.read_text(encoding="utf-8").strip().rstrip("/")
        except OSError:
            continue
        if value.startswith(("https://", "http://")):
            return value
    return None


def _available_omni_hosts(
    hosts: Sequence[JsonObject],
) -> tuple[dict[str, JsonObject], dict[str, JsonObject], dict[str, list[JsonObject]]]:
    """Index online, user-managed Omni hosts by stable and legacy identities."""
    by_coder_workspace_id: dict[str, JsonObject] = {}
    by_host_id: dict[str, JsonObject] = {}
    by_legacy_name: dict[str, list[JsonObject]] = {}
    for host in hosts:
        host_id = host.get("host_id")
        if (
            not isinstance(host_id, str)
            or not host_id
            or host.get("status") != "online"
            or host.get("sandbox_provider") is not None
        ):
            continue
        coder_workspace_id = host.get("coder_workspace_id")
        if isinstance(coder_workspace_id, str) and coder_workspace_id:
            by_coder_workspace_id[coder_workspace_id.lower()] = host
            continue
        by_host_id[host_id.lower()] = host
        name = host.get("name")
        if isinstance(name, str) and (normalized_name := name.strip().casefold()):
            by_legacy_name.setdefault(normalized_name, []).append(host)
    return by_coder_workspace_id, by_host_id, by_legacy_name


def _match_omni_host(
    *,
    workspace_id: str,
    workspace_name: str,
    by_coder_workspace_id: Mapping[str, JsonObject],
    by_host_id: Mapping[str, JsonObject],
    by_legacy_name: Mapping[str, Sequence[JsonObject]],
) -> tuple[JsonObject, str] | None:
    """Match a Coder workspace to an online Omni host without ambiguity."""
    normalized_id = workspace_id.lower()
    host = by_coder_workspace_id.get(normalized_id)
    if host is not None:
        return host, "coder_workspace_id"
    host = by_host_id.get(normalized_id)
    if host is not None:
        return host, "host_id"
    name_matches = by_legacy_name.get(workspace_name.strip().casefold(), ())
    if len(name_matches) == 1:
        return name_matches[0], "unique_workspace_name"
    return None


def _workspace_agents(workspace: JsonObject) -> list[JsonObject]:
    """Flatten agents from a workspace's latest-build resources."""
    latest_build = _json_object(workspace.get("latest_build")) or {}
    agents: list[JsonObject] = []
    for resource in _objects(latest_build.get("resources")):
        agents.extend(_objects(resource.get("agents")))
    return agents


def _workspace_is_running(workspace: JsonObject) -> bool:
    """Return whether Coder reports a healthy running workspace."""
    latest_build = _json_object(workspace.get("latest_build")) or {}
    health = _json_object(workspace.get("health")) or {}
    return latest_build.get("status") == "running" and health.get("healthy") is True


def _agent_is_ready(agent: JsonObject) -> bool:
    """Return whether a workspace agent is connected and ready."""
    lifecycle = agent.get("lifecycle_state")
    return agent.get("status") == "connected" and lifecycle == "ready"


async def _ssh_probe(owner: str, workspace: str) -> JsonObject | None:
    """Collect bounded fallback facts from one Coder workspace over SSH."""
    if shutil.which("coder") is None:
        return None
    target = f"{owner}/{workspace}" if owner else workspace
    try:
        process = await asyncio.create_subprocess_exec(
            "coder",
            "ssh",
            "--disable-autostart",
            target,
            "--",
            "sh",
            "-s",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return None
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(_SSH_PROBE.encode()), timeout=_SSH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return None
    if process.returncode != 0:
        return None
    return _parse_ssh_probe_output(stdout)


def _parse_ssh_probe_output(stdout: bytes) -> JsonObject | None:
    """Parse the fixed SSH probe output without exposing raw repository URLs."""
    lines = stdout.decode(errors="replace").splitlines()
    stats = next((line.split("\t", 6) for line in lines if line.startswith("stats\t")), None)
    if stats is None or len(stats) != 7:
        return None

    def _float(text: str) -> float | None:
        try:
            return float(text) if text else None
        except ValueError:
            return None

    memory_available_kib = _float(stats[1])
    physical_pages = _float(stats[2])
    page_size = _float(stats[3])
    memory_total_bytes = (
        physical_pages * page_size
        if physical_pages is not None and page_size is not None
        else None
    )
    memory_used_bytes = None
    if memory_available_kib is not None and memory_total_bytes is not None:
        memory_used_bytes = max(0.0, memory_total_bytes - memory_available_kib * 1024)
    repositories: list[JsonObject] = []
    for line in lines:
        fields = line.split("\t", 2)
        if len(fields) != 3 or fields[0] != "repo" or not fields[1].startswith("/"):
            continue
        remote = _normalize_git_remote(fields[2])
        if remote is not None:
            repositories.append({"workspace_path": fields[1], "repository_remote": remote})
    return {
        "memory_used_bytes": memory_used_bytes,
        "memory_total_bytes": memory_total_bytes,
        "load_1m": _float(stats[4]),
        "logical_cpu_count": _float(stats[5]),
        "containers": _float(stats[6]),
        "repositories": repositories,
    }


def _select_probe_repository(
    probe: JsonObject,
    *,
    expected_repository: str | None,
    reported_directory: object,
) -> JsonObject | None:
    """Select a verified source checkout from one SSH probe result."""
    repositories = _objects(probe.get("repositories"))
    if expected_repository is not None:
        repositories = [
            repository
            for repository in repositories
            if repository.get("repository_remote") == expected_repository
        ]
    elif isinstance(reported_directory, str):
        reported_match = [
            repository
            for repository in repositories
            if repository.get("workspace_path") == reported_directory
        ]
        if reported_match:
            repositories = reported_match
        elif len(repositories) != 1:
            return None
    elif len(repositories) != 1:
        return None
    if not repositories:
        return None
    return min(
        repositories,
        key=lambda repository: (
            "/.worktrees/" in str(repository.get("workspace_path")),
            len(str(repository.get("workspace_path"))),
            str(repository.get("workspace_path")),
        ),
    )


async def _container_count(client: httpx.AsyncClient, agent_id: str) -> int | None:
    """Read containers visible to a Coder workspace agent."""
    try:
        response = await client.get(
            f"/api/v2/workspaceagents/{agent_id}/containers",
            timeout=_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    containers = payload.get("containers") if isinstance(payload, dict) else payload
    return len(containers) if isinstance(containers, list) else None


async def discover_coder_hosts(
    *,
    server_client: httpx.AsyncClient,
    memory_key: str = DEFAULT_MEMORY_KEY,
    cpu_key: str = DEFAULT_CPU_KEY,
    load_key: str = DEFAULT_LOAD_KEY,
    containers_key: str = DEFAULT_CONTAINERS_KEY,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    requested_memory_gib: float = DEFAULT_REQUESTED_MEMORY_GIB,
    memory_reserve_gib: float = DEFAULT_MEMORY_RESERVE_GIB,
    ssh_fallback: bool = True,
    repository_remote: str | None = None,
) -> JsonObject:
    """Return ranked Coder workspaces that also have an online Omni host.

    Memory is an advisory eligibility check. CPU, logical CPU count, load, and
    containers only rank otherwise eligible workspaces; none is a hard session
    limit. When every host is over the memory target or unmeasured, callers must
    request explicit human confirmation before overriding the recommendation.
    """
    coder_url = _coder_url()
    expected_repository = _normalize_git_remote(repository_remote)
    if not coder_url:
        return {
            "error": "coder_not_configured",
            "detail": "set CODER_URL or log in with the coder CLI",
        }
    token = await asyncio.to_thread(_coder_token)
    if token is None:
        return {
            "error": "coder_not_authenticated",
            "detail": "set CODER_SESSION_TOKEN or log in with the coder CLI",
        }

    try:
        hosts_response = await server_client.get("/v1/hosts", timeout=_API_TIMEOUT_SECONDS)
        hosts_response.raise_for_status()
        hosts_payload = hosts_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": "omni_hosts_unavailable", "detail": str(exc)}
    omni_hosts = _objects(hosts_payload.get("hosts") if isinstance(hosts_payload, dict) else None)
    by_workspace, by_host_id, by_legacy_name = _available_omni_hosts(omni_hosts)

    def _host_match(workspace_id: str, workspace_name: str) -> tuple[JsonObject, str] | None:
        return _match_omni_host(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            by_coder_workspace_id=by_workspace,
            by_host_id=by_host_id,
            by_legacy_name=by_legacy_name,
        )

    requested_keys = tuple(
        dict.fromkeys(
            (
                memory_key,
                cpu_key,
                load_key,
                containers_key,
                *_MEMORY_ALIASES,
                *_CPU_ALIASES,
                *_LOAD_ALIASES,
                *_CONTAINER_ALIASES,
            )
        )
    )
    base_query = "owner:me status:running healthy:true"
    query = (
        base_query
        + " "
        + " ".join(f'include_agent_metadata:"{key}"' for key in requested_keys if key)
    )
    headers = {"Coder-Session-Token": token}
    async with httpx.AsyncClient(base_url=coder_url, headers=headers) as coder_client:
        try:
            response = await coder_client.get(
                "/api/v2/workspaces",
                params={"q": query},
                timeout=_API_TIMEOUT_SECONDS,
            )
            if response.status_code == 400:
                # Coder releases before metadata search expansion reject the
                # include_agent_metadata term. The fixed SSH probe supplies the
                # same placement facts, so retry the portable workspace query.
                response = await coder_client.get(
                    "/api/v2/workspaces",
                    params={"q": base_query},
                    timeout=_API_TIMEOUT_SECONDS,
                )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"error": "coder_api_failed", "detail": str(exc)}

        workspaces = _objects(payload.get("workspaces") if isinstance(payload, dict) else payload)
        probe_tasks: dict[str, asyncio.Task[JsonObject | None]] = {}
        if ssh_fallback:
            probe_limit = asyncio.Semaphore(4)

            async def _bounded_probe(owner: str, workspace_name: str) -> JsonObject | None:
                async with probe_limit:
                    return await _ssh_probe(owner, workspace_name)

            for workspace in workspaces:
                workspace_id = workspace.get("id")
                workspace_name = workspace.get("name")
                if (
                    not isinstance(workspace_id, str)
                    or not isinstance(workspace_name, str)
                    or _host_match(workspace_id, workspace_name) is None
                    or not _workspace_is_running(workspace)
                    or not any(_agent_is_ready(agent) for agent in _workspace_agents(workspace))
                ):
                    continue
                owner_value = workspace.get("owner_name") or workspace.get("owner")
                owner_name = owner_value if isinstance(owner_value, str) else ""
                probe_tasks[workspace_id] = asyncio.create_task(
                    _bounded_probe(owner_name, workspace_name)
                )

        candidates: list[JsonObject] = []
        excluded: list[JsonObject] = []
        for workspace in workspaces:
            workspace_id = workspace.get("id")
            workspace_name = workspace.get("name")
            owner_value = workspace.get("owner_name") or workspace.get("owner")
            owner = owner_value if isinstance(owner_value, str) else ""
            if not isinstance(workspace_id, str) or not isinstance(workspace_name, str):
                continue
            host_match = _host_match(workspace_id, workspace_name)
            if host_match is None:
                excluded.append(
                    {
                        "workspace_id": workspace_id,
                        "workspace_name": workspace_name,
                        "reason": "no_online_omni_host",
                    }
                )
                continue
            host, identity_match = host_match
            if not _workspace_is_running(workspace):
                excluded.append(
                    {
                        "workspace_id": workspace_id,
                        "workspace_name": workspace_name,
                        "reason": "coder_workspace_not_healthy_running",
                    }
                )
                continue
            ready_agents = [
                agent for agent in _workspace_agents(workspace) if _agent_is_ready(agent)
            ]
            if not ready_agents:
                excluded.append(
                    {
                        "workspace_id": workspace_id,
                        "workspace_name": workspace_name,
                        "reason": "coder_agent_not_ready",
                    }
                )
                continue
            agent = ready_agents[0]
            metadata = _metadata_entries(agent)
            memory_raw = _fresh_metadata_value(
                metadata, (memory_key, *_MEMORY_ALIASES), max_age_seconds
            )
            cpu_raw = _fresh_metadata_value(metadata, (cpu_key, *_CPU_ALIASES), max_age_seconds)
            load_raw = _fresh_metadata_value(metadata, (load_key, *_LOAD_ALIASES), max_age_seconds)
            containers_raw = _fresh_metadata_value(
                metadata, (containers_key, *_CONTAINER_ALIASES), max_age_seconds
            )
            memory_used, memory_total = _parse_memory(memory_raw)
            cpu_percent = _parse_percent(cpu_raw)
            load_1m = _parse_number(load_raw)
            parsed_containers = _parse_number(containers_raw)
            containers = int(parsed_containers) if parsed_containers is not None else None
            agent_id = agent.get("id")
            if containers is None and isinstance(agent_id, str):
                containers = await _container_count(coder_client, agent_id)

            reported_workspace_path = agent.get("expanded_directory") or agent.get("directory")
            workspace_path: object = None
            candidate_repository: object = None
            source = "coder_metadata"
            warnings: list[str] = []
            probe_task = probe_tasks.get(workspace_id)
            if probe_task is not None:
                probe = await probe_task
                if probe is not None:
                    if memory_used is None:
                        memory_used = cast(float | None, probe.get("memory_used_bytes"))
                    if memory_total is None:
                        memory_total = cast(float | None, probe.get("memory_total_bytes"))
                    if load_1m is None:
                        load_1m = cast(float | None, probe.get("load_1m"))
                    if containers is None and probe.get("containers") is not None:
                        containers = int(cast(float, probe["containers"]))
                    selected_repository = _select_probe_repository(
                        probe,
                        expected_repository=expected_repository,
                        reported_directory=reported_workspace_path,
                    )
                    if selected_repository is not None:
                        workspace_path = selected_repository.get("workspace_path")
                        candidate_repository = selected_repository.get("repository_remote")
                    logical_cpu_count = probe.get("logical_cpu_count")
                    source = "coder_metadata+ssh"
                else:
                    logical_cpu_count = None
                    warnings.append("ssh_fallback_failed")
            else:
                logical_cpu_count = None
                if ssh_fallback:
                    warnings.append("ssh_fallback_unavailable")
                else:
                    warnings.append("repository_path_unverified")

            available_gib: float | None = None
            memory_ratio: float | None = None
            if memory_used is not None and memory_total is not None and memory_total > 0:
                available_gib = (memory_total - memory_used) / (1024.0**3)
                memory_ratio = memory_used / memory_total
            required_gib = requested_memory_gib + memory_reserve_gib
            if available_gib is None:
                eligible = False
                capacity_reason = "memory_unknown"
            elif available_gib < required_gib:
                eligible = False
                capacity_reason = "insufficient_advisory_memory"
            elif not isinstance(workspace_path, str) or not workspace_path.startswith("/"):
                eligible = False
                capacity_reason = "repository_path_unverified"
            elif expected_repository is not None and candidate_repository is None:
                eligible = False
                capacity_reason = "repository_identity_unverified"
            elif expected_repository is not None and candidate_repository != expected_repository:
                eligible = False
                capacity_reason = "repository_mismatch"
            else:
                eligible = True
                capacity_reason = "eligible"

            normalized_load = None
            if isinstance(load_1m, (int, float)) and isinstance(logical_cpu_count, (int, float)):
                if logical_cpu_count > 0:
                    normalized_load = float(load_1m) / float(logical_cpu_count)
            candidates.append(
                {
                    "host_id": host.get("host_id"),
                    "host_name": host.get("name"),
                    "identity_match": identity_match,
                    "workspace_id": workspace_id,
                    "workspace_name": workspace_name,
                    "owner_name": owner,
                    "agent_id": agent_id,
                    "agent_name": agent.get("name"),
                    "workspace_path": workspace_path,
                    "repository_remote": candidate_repository,
                    "coder_reported_directory": reported_workspace_path,
                    "memory_raw": memory_raw,
                    "memory_used_gib": (
                        round(memory_used / (1024.0**3), 2) if memory_used is not None else None
                    ),
                    "memory_total_gib": (
                        round(memory_total / (1024.0**3), 2) if memory_total is not None else None
                    ),
                    "memory_available_gib": (
                        round(available_gib, 2) if available_gib is not None else None
                    ),
                    "cpu_raw": cpu_raw,
                    "cpu_percent": cpu_percent,
                    "load_raw": load_raw,
                    "load_1m": load_1m,
                    "logical_cpu_count": logical_cpu_count,
                    "normalized_load": normalized_load,
                    "containers": containers,
                    "eligible": eligible,
                    "capacity_reason": capacity_reason,
                    "source": source,
                    "warnings": warnings,
                    "_memory_ratio": memory_ratio,
                }
            )

    candidates.sort(
        key=lambda candidate: (
            not bool(candidate.get("eligible")),
            candidate.get("_memory_ratio") is None,
            candidate.get("_memory_ratio") if candidate.get("_memory_ratio") is not None else 2.0,
            candidate.get("cpu_percent") if candidate.get("cpu_percent") is not None else 101.0,
            candidate.get("normalized_load")
            if candidate.get("normalized_load") is not None
            else 101.0,
            candidate.get("containers") if candidate.get("containers") is not None else 1_000_000,
            str(candidate.get("workspace_name") or ""),
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate.pop("_memory_ratio", None)
        candidate["rank"] = rank
    return {
        "candidates": candidates,
        "excluded": excluded,
        "requested_memory_gib": requested_memory_gib,
        "memory_reserve_gib": memory_reserve_gib,
        "expected_repository_remote": expected_repository,
        "needs_confirmation": not any(bool(item.get("eligible")) for item in candidates),
        "ranking_note": (
            "memory is advisory; CPU, logical CPU count, load, and containers rank hosts "
            "but never cap coding-agent sessions"
        ),
    }
