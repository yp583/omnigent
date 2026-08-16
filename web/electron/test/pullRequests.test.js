"use strict";

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const {
  ciStatus,
  githubRepository,
  listPullRequests,
  validateSessions,
} = require("../src/pullRequests");

describe("pull request discovery", () => {
  it("accepts GitHub HTTPS and SSH remotes only", () => {
    assert.equal(githubRepository("git@github.com:acme/widget.git"), "acme/widget");
    assert.equal(githubRepository("https://github.com/acme/widget.git"), "acme/widget");
    assert.equal(githubRepository("https://gitlab.com/acme/widget.git"), null);
  });

  it("rejects relative workspaces and malformed branches", () => {
    assert.deepEqual(
      validateSessions([
        { sessionId: "good", workspace: "/tmp/worktree", branch: "feature/ui" },
        { sessionId: "relative", workspace: "./worktree", branch: "main" },
        { sessionId: "newline", workspace: "/tmp/worktree", branch: "bad\nbranch" },
      ]),
      [{ sessionId: "good", workspace: "/tmp/worktree", branch: "feature/ui" }],
    );
  });

  it("collapses check rollups to a compact state", () => {
    assert.equal(ciStatus([{ conclusion: "SUCCESS" }]), "passing");
    assert.equal(ciStatus([{ status: "IN_PROGRESS" }]), "pending");
    assert.equal(ciStatus([{ conclusion: "FAILURE" }]), "failing");
    assert.equal(ciStatus([]), "unknown");
  });

  it("uses only fixed git/gh commands and deduplicates a PR across sessions", async () => {
    const calls = [];
    const execFile = (command, args, _options, callback) => {
      calls.push([command, args]);
      if (command === "git") {
        callback(null, "git@github.com:acme/widget.git\n", "");
        return;
      }
      callback(
        null,
        JSON.stringify([
          {
            number: 42,
            title: "Fast agent rail",
            url: "https://github.com/acme/widget/pull/42",
            headRefName: "feature/ui",
            headRefOid: "abc123",
            additions: 18,
            deletions: 4,
            statusCheckRollup: [{ conclusion: "SUCCESS" }],
          },
        ]),
        "",
      );
    };

    const result = await listPullRequests(
      {
        sessions: [
          { sessionId: "root", workspace: "/tmp/root", branch: "feature/ui" },
          { sessionId: "child", workspace: "/tmp/child", branch: "feature/ui" },
        ],
      },
      { execFile },
    );

    assert.equal(result.pullRequests.length, 1);
    assert.deepEqual(result.pullRequests[0].sourceSessionIds, ["root", "child"]);
    assert.equal(result.pullRequests[0].ciStatus, "passing");
    assert.ok(calls.every(([command]) => command === "git" || command === "gh"));
    assert.ok(calls.every(([, args]) => Array.isArray(args)));
  });
});
