"""Tests for ``build-omnigent`` skill injection into native agent bundles.

The injector resolved its source path by counting ``.parent``s off its own
module file. Moving the module one package deeper left the count stale, so
the source directory did not exist and the ``not source.is_dir()`` guard
returned on every call — silently injecting nothing for every
``omnigent claude`` / ``omnigent codex`` user.

These tests pin the observable outcome (the skill lands in the bundle and
the Codex consumer resolves it) rather than the path expression, so the
next move of the module fails here instead of in the field.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

import omnigent
from omnigent.inner.codex_executor import codex_skill_sources, select_codex_skill_dirs
from omnigent.runner.native.orchestration import (
    _ensure_orchestrator_skills_in_bundle,
)
from omnigent.runner.tool_dispatch import _inject_orchestrator_skills
from omnigent.spec.types import AgentSpec

SKILL_NAME = "build-omnigent"
CLOUD_DISPATCH_SKILL_NAME = "cloud-dispatch"
CODER_DISPATCH_ALIAS_NAME = "coder-dispatch"


def test_skill_source_lives_in_the_installed_package() -> None:
    """The canonical source directory ships inside the package."""
    source = Path(omnigent.__file__).resolve().parent / "onboarding" / "agent" / "skills"
    assert (source / SKILL_NAME / "SKILL.md").is_file()
    assert (source / "_orchestration" / CLOUD_DISPATCH_SKILL_NAME / "SKILL.md").is_file()


def test_onboarding_agent_tree_is_declared_as_package_data() -> None:
    """Git-installed wheels carry sources without local bytecode caches."""
    repo_root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = set(project["tool"]["setuptools"]["package-data"]["omnigent.onboarding"])
    assert {
        "agent/skills/*/SKILL.md",
        "agent/skills/_orchestration/*/SKILL.md",
    } <= package_data
    assert not any("**/*" in pattern for pattern in package_data)
    package_find = project["tool"]["setuptools"]["packages"]["find"]
    assert "*.__pycache__" in package_find["exclude"]


def test_injects_skill_into_empty_bundle(tmp_path: Path) -> None:
    """A bare bundle gets a usable ``skills/build-omnigent`` with content."""
    _ensure_orchestrator_skills_in_bundle(tmp_path, None)

    target = tmp_path / "skills" / SKILL_NAME
    assert target.is_dir(), f"{SKILL_NAME} was not injected into the bundle"
    assert (target / "SKILL.md").is_file(), "injected skill has no readable SKILL.md"


def test_injection_is_idempotent(tmp_path: Path) -> None:
    """Re-running on the same bundle neither raises nor duplicates."""
    _ensure_orchestrator_skills_in_bundle(tmp_path, None)
    _ensure_orchestrator_skills_in_bundle(tmp_path, None)

    assert (tmp_path / "skills" / SKILL_NAME).is_dir()
    assert [p.name for p in (tmp_path / "skills").iterdir()] == [SKILL_NAME]


def test_codex_resolves_the_injected_skill(tmp_path: Path) -> None:
    """The injected skill reaches Codex's real skill-source resolution.

    Guards the downstream half: ``codex_skill_sources`` only returns the
    bundle root when ``<bundle>/skills`` exists, so an injector that links
    nothing drops the skill even though both sides look correct alone.
    """
    _ensure_orchestrator_skills_in_bundle(tmp_path, None)

    sources = codex_skill_sources(tmp_path, tmp_path / "fake-home")
    assert sources == [tmp_path / "skills"]
    assert SKILL_NAME in select_codex_skill_dirs("all", sources)


def test_spawn_agent_receives_cloud_dispatch_and_compatibility_alias(tmp_path: Path) -> None:
    """Only an agent with arbitrary-child spawn gets canonical dispatch."""
    _ensure_orchestrator_skills_in_bundle(
        tmp_path,
        AgentSpec(spec_version=1, spawn=True),
    )

    for skill_name in (CLOUD_DISPATCH_SKILL_NAME, CODER_DISPATCH_ALIAS_NAME):
        target = tmp_path / "skills" / skill_name
        assert target.is_dir()
        assert (target / "SKILL.md").is_file()


def test_runner_tool_surface_injects_cloud_dispatch_and_alias() -> None:
    """SDK/in-process runners receive the same canonical and alias skills."""
    skills = _inject_orchestrator_skills([], AgentSpec(spec_version=1, spawn=True))

    names = {skill.name for skill in skills}
    assert CLOUD_DISPATCH_SKILL_NAME in names
    assert CODER_DISPATCH_ALIAS_NAME in names


def test_cloud_dispatch_skill_pins_provider_box_and_safety_contract() -> None:
    """The packaged skill preserves the Coder dispatch safety boundary."""
    source = Path(omnigent.__file__).resolve().parent / "onboarding" / "agent" / "skills"
    content = (source / "_orchestration" / CLOUD_DISPATCH_SKILL_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "sys_agent_list" in content
    assert "sys_coder_hosts" in content
    assert "sys_session_create" in content
    assert "exact git base revision" in content
    assert "exact `host_id`" in content
    assert "`coder_workspace_id`" in content
    assert "Never reroute a pinned dispatch" in content
    assert "Refuse `pi`" in content
    assert "isolated worktree" in content
    assert "`detached: true`" in content
    assert "main session list" in content
    assert "Never run `git remote get-url` without" in content
    assert "Sanitize repository URLs before they reach stdout" in content


def test_coder_dispatch_alias_names_removal_version() -> None:
    """The compatibility alias redirects and records its removal release."""
    source = Path(omnigent.__file__).resolve().parent / "onboarding" / "agent" / "skills"
    content = (source / "_orchestration" / CODER_DISPATCH_ALIAS_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "deprecated" in content.lower()
    assert "v0.12.0" in content
    assert "cloud-dispatch" in content


def test_non_spawn_agent_does_not_receive_coder_dispatch_skill(tmp_path: Path) -> None:
    """The skill is hidden when its required placement tools are absent."""
    _ensure_orchestrator_skills_in_bundle(
        tmp_path,
        AgentSpec(spec_version=1, spawn=False),
    )

    assert not (tmp_path / "skills" / CLOUD_DISPATCH_SKILL_NAME).exists()
    assert not (tmp_path / "skills" / CODER_DISPATCH_ALIAS_NAME).exists()
