"use strict";

const path = require("node:path");
const { execFile: nodeExecFile } = require("node:child_process");

const MAX_SESSIONS = 64;
const MAX_OUTPUT_BYTES = 1024 * 1024;
const COMMAND_TIMEOUT_MS = 8_000;

function execText(command, args, execFile = nodeExecFile) {
  return new Promise((resolve, reject) => {
    execFile(
      command,
      args,
      { encoding: "utf8", maxBuffer: MAX_OUTPUT_BYTES, timeout: COMMAND_TIMEOUT_MS },
      (error, stdout, stderr) => {
        if (error) {
          error.stderr = stderr;
          reject(error);
          return;
        }
        resolve(String(stdout).trim());
      },
    );
  });
}

function githubRepository(remote) {
  const value = String(remote ?? "").trim();
  const scp = value.match(/^git@github\.com:([^/\s]+)\/([^\s]+?)(?:\.git)?$/i);
  if (scp) return `${scp[1]}/${scp[2]}`;
  try {
    const url = new URL(value);
    if (url.hostname.toLowerCase() !== "github.com") return null;
    const parts = url.pathname
      .replace(/^\/+|\/+$/g, "")
      .replace(/\.git$/i, "")
      .split("/");
    if (parts.length !== 2 || parts.some((part) => !part)) return null;
    return `${parts[0]}/${parts[1]}`;
  } catch {
    return null;
  }
}

function ciStatus(rollup) {
  if (!Array.isArray(rollup) || rollup.length === 0) return "unknown";
  const states = rollup.map((item) =>
    String(item?.conclusion ?? item?.state ?? item?.status ?? "").toUpperCase(),
  );
  if (states.some((state) => ["FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"].includes(state))) {
    return "failing";
  }
  if (states.some((state) => ["PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED"].includes(state))) {
    return "pending";
  }
  if (states.every((state) => ["SUCCESS", "NEUTRAL", "SKIPPED"].includes(state))) {
    return "passing";
  }
  return "unknown";
}

function validateSessions(value) {
  if (!Array.isArray(value) || value.length > MAX_SESSIONS) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const sessionId = typeof entry.sessionId === "string" ? entry.sessionId : "";
    const workspace = typeof entry.workspace === "string" ? entry.workspace : "";
    const branch = typeof entry.branch === "string" ? entry.branch : "";
    if (!sessionId || sessionId.length > 200 || !path.isAbsolute(workspace)) return [];
    if (workspace.includes("\0") || branch.includes("\0") || branch.includes("\n")) return [];
    if (workspace.length > 4096 || branch.length > 255) return [];
    return [{ sessionId, workspace, branch }];
  });
}

function friendlyGhError(error) {
  const detail = `${error?.message ?? ""}\n${error?.stderr ?? ""}`.toLowerCase();
  if (detail.includes("auth") || detail.includes("login") || detail.includes("token")) {
    return "GitHub CLI needs authentication. Run `gh auth login` on this Mac.";
  }
  if (detail.includes("enoent") || detail.includes("not found")) {
    return "Install GitHub CLI (`gh`) to track pull requests.";
  }
  return "Pull request status is temporarily unavailable.";
}

async function listPullRequests(request, options = {}) {
  const execFile = options.execFile ?? nodeExecFile;
  const sessions = validateSessions(request?.sessions);
  const found = new Map();
  let ghError = null;

  const discoveries = await Promise.all(
    sessions.map(async (session) => {
      let repository;
      let branch = session.branch;
      try {
        const remote = await execText(
          "git",
          ["-C", session.workspace, "remote", "get-url", "origin"],
          execFile,
        );
        repository = githubRepository(remote);
        if (!repository) return null;
        if (!branch) {
          branch = await execText(
            "git",
            ["-C", session.workspace, "branch", "--show-current"],
            execFile,
          );
        }
        if (!branch) return null;
      } catch {
        // A session can have no checkout, or its remote can be unavailable.
        // Skip that workspace without hiding valid PRs from sibling sessions.
        return null;
      }

      try {
        const raw = await execText(
          "gh",
          [
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "10",
            "--json",
            "number,title,url,headRefName,headRefOid,additions,deletions,statusCheckRollup",
          ],
          execFile,
        );
        const rows = JSON.parse(raw || "[]");
        return { session, repository, branch, rows: Array.isArray(rows) ? rows : [] };
      } catch (error) {
        return { session, repository, branch, rows: [], error };
      }
    }),
  );

  for (const discovery of discoveries) {
    if (!discovery) continue;
    const { session, repository, branch, rows, error } = discovery;
    if (error) ghError ??= friendlyGhError(error);
    for (const row of rows) {
      if (
        typeof row?.number !== "number" ||
        typeof row?.title !== "string" ||
        typeof row?.url !== "string"
      ) {
        continue;
      }
      const key = `${repository}#${row.number}`;
      const existing = found.get(key);
      if (existing) {
        if (!existing.sourceSessionIds.includes(session.sessionId)) {
          existing.sourceSessionIds.push(session.sessionId);
        }
        continue;
      }
      found.set(key, {
        repository,
        number: row.number,
        title: row.title,
        url: row.url,
        branch: typeof row.headRefName === "string" ? row.headRefName : branch,
        headSha: typeof row.headRefOid === "string" ? row.headRefOid : "",
        additions: Number.isFinite(row.additions) ? row.additions : 0,
        deletions: Number.isFinite(row.deletions) ? row.deletions : 0,
        ciStatus: ciStatus(row.statusCheckRollup),
        sourceSessionIds: [session.sessionId],
      });
    }
  }

  return { pullRequests: [...found.values()], ...(ghError ? { error: ghError } : {}) };
}

module.exports = {
  ciStatus,
  githubRepository,
  listPullRequests,
  validateSessions,
};
