import { describe, expect, it } from "vitest";

import {
  CODEX_NATIVE_BYPASS_APPROVAL_VALUE,
  CODEX_NATIVE_BYPASS_SANDBOX_LABEL_KEY,
  mergeNativePermissionModeArgs,
  NATIVE_PERMISSION_CUSTOM_VALUE,
  nativePermissionControlForHarness,
  nativePermissionModeFromSession,
} from "./nativePermissionModes";

describe("nativePermissionControlForHarness", () => {
  it.each([
    ["claude-native", "Permissions"],
    ["codex-native", "Approval"],
    ["cursor-native", "Mode"],
    ["antigravity-native", "Permissions"],
  ])("exposes the harness-specific control for %s", (harness, label) => {
    expect(nativePermissionControlForHarness(harness)?.label).toBe(label);
  });

  it("does not invent a permission mapping for unsupported harnesses", () => {
    expect(nativePermissionControlForHarness("opencode-native")).toBeNull();
  });
});

describe("nativePermissionModeFromSession", () => {
  it("reads each Claude permission mode from launch argv", () => {
    expect(
      nativePermissionModeFromSession("claude-native", ["--permission-mode", "acceptEdits"]),
    ).toBe("acceptEdits");
  });

  it("reads Codex presets and the session-scoped bypass label", () => {
    expect(
      nativePermissionModeFromSession("codex-native", [
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "on-request",
      ]),
    ).toBe("read-only");
    expect(
      nativePermissionModeFromSession("codex-native", [], {
        [CODEX_NATIVE_BYPASS_SANDBOX_LABEL_KEY]: "1",
      }),
    ).toBe(CODEX_NATIVE_BYPASS_APPROVAL_VALUE);
  });

  it("keeps an arbitrary Codex permission profile visible as Custom", () => {
    expect(
      nativePermissionModeFromSession("codex-native", [
        "-c",
        'default_permissions="dev"',
        "-c",
        'approvals_reviewer="auto_review"',
      ]),
    ).toBe(NATIVE_PERMISSION_CUSTOM_VALUE);
  });
});

describe("mergeNativePermissionModeArgs", () => {
  it("replaces Claude's mode without dropping unrelated launch flags", () => {
    expect(
      mergeNativePermissionModeArgs(
        "claude-native",
        ["--model", "opus", "--permission-mode", "plan", "--effort", "high"],
        "acceptEdits",
      ),
    ).toEqual(["--model", "opus", "--effort", "high", "--permission-mode", "acceptEdits"]);
  });

  it("replaces Codex permission profiles while preserving provider config", () => {
    expect(
      mergeNativePermissionModeArgs(
        "codex-native",
        [
          "-c",
          'model_provider="omnigent"',
          "-c",
          'default_permissions="dev"',
          "-c",
          'approvals_reviewer="auto_review"',
        ],
        "read-only",
      ),
    ).toEqual([
      "-c",
      'model_provider="omnigent"',
      "--sandbox",
      "read-only",
      "--ask-for-approval",
      "on-request",
    ]);
  });

  it("can return Cursor and Antigravity to their defaults", () => {
    expect(
      mergeNativePermissionModeArgs("cursor-native", ["--model", "x", "--yolo"], "default"),
    ).toEqual(["--model", "x"]);
    expect(
      mergeNativePermissionModeArgs(
        "antigravity-native",
        ["--dangerously-skip-permissions", "--resume", "thread"],
        "default",
      ),
    ).toEqual(["--resume", "thread"]);
  });
});
