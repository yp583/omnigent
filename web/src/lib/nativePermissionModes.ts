/**
 * Native-harness permission / execution modes shared by session creation and
 * the in-session composer config. These values mirror each vendor CLI's launch
 * flags; keep the option lists in sync with the corresponding ``--help``.
 */

export interface NativePermissionModeOption {
  value: string;
  label: string;
  description: string;
  args: string[];
}

export const CLAUDE_NATIVE_DEFAULT_PERMISSION_MODE = "default";
export const CLAUDE_NATIVE_PERMISSION_MODES: NativePermissionModeOption[] = [
  {
    value: "default",
    label: "Default",
    description: "Prompts before edits and commands",
    args: [],
  },
  {
    value: "auto",
    label: "Auto",
    description: "Auto-runs; a classifier blocks risky actions",
    args: ["--permission-mode", "auto"],
  },
  {
    value: "acceptEdits",
    label: "Accept edits",
    description: "Auto-applies file edits; commands still prompt",
    args: ["--permission-mode", "acceptEdits"],
  },
  {
    value: "plan",
    label: "Plan",
    description: "Plans only; makes no edits",
    args: ["--permission-mode", "plan"],
  },
  {
    value: "dontAsk",
    label: "Don't ask",
    description: "Auto-denies anything not pre-approved",
    args: ["--permission-mode", "dontAsk"],
  },
  {
    value: "bypassPermissions",
    label: "Bypass permissions",
    description: "Runs everything; no prompts or safety checks",
    args: ["--permission-mode", "bypassPermissions"],
  },
];

export const AGY_NATIVE_DEFAULT_SKIP_MODE = "default";
export const AGY_NATIVE_SKIP_VALUE = "skip";
export const AGY_NATIVE_SKIP_MODES: NativePermissionModeOption[] = [
  {
    value: AGY_NATIVE_DEFAULT_SKIP_MODE,
    label: "Ask every time",
    description: "Prompts before each tool runs",
    args: [],
  },
  {
    value: AGY_NATIVE_SKIP_VALUE,
    label: "Skip permissions",
    description: "Runs everything; no prompts or safety checks",
    args: ["--dangerously-skip-permissions"],
  },
];

export const CURSOR_NATIVE_DEFAULT_EXEC_MODE = "default";
export const CURSOR_NATIVE_EXEC_MODES: NativePermissionModeOption[] = [
  {
    value: "default",
    label: "Default",
    description: "Normal agent mode; prompts before running commands",
    args: [],
  },
  {
    value: "auto-review",
    label: "Auto-review",
    description: "Smart Auto: auto-runs safe tool calls and prompts for the rest",
    args: ["--auto-review"],
  },
  {
    value: "plan",
    label: "Plan",
    description: "Read-only planning; analyzes and proposes plans, no edits",
    args: ["--mode", "plan"],
  },
  {
    value: "ask",
    label: "Ask",
    description: "Q&A style; explains and answers questions (read-only)",
    args: ["--mode", "ask"],
  },
  {
    value: "yolo",
    label: "Yolo",
    description: "Runs everything without prompts or safety checks",
    args: ["--yolo"],
  },
];

export const CODEX_NATIVE_DEFAULT_APPROVAL_MODE = "default";
export const CODEX_NATIVE_APPROVAL_MODES: NativePermissionModeOption[] = [
  {
    value: "default",
    label: "Default",
    description: "Read/edit/run in workspace; approval for external edits or network",
    args: [],
  },
  {
    value: "full-access",
    label: "Full access",
    description: "Edit any file and access the internet without approval",
    args: ["--sandbox", "danger-full-access", "--ask-for-approval", "never"],
  },
  {
    value: "read-only",
    label: "Read only",
    description: "Read files only; approval required for edits, commands, or network",
    args: ["--sandbox", "read-only", "--ask-for-approval", "on-request"],
  },
];

export const CODEX_NATIVE_BYPASS_SANDBOX_LABEL_KEY = "omnigent.codex_native.bypass_sandbox";
export const CODEX_NATIVE_BYPASS_APPROVAL_VALUE = "bypass";
export const CODEX_NATIVE_BYPASS_APPROVAL_OPTION: NativePermissionModeOption = {
  value: CODEX_NATIVE_BYPASS_APPROVAL_VALUE,
  label: "Bypass approvals & sandbox",
  description: "Runs Codex with no approval prompts and no command sandbox",
  // The runner owns this launch flag via the session label above. Keeping it
  // out of argv preserves the existing create-session contract.
  args: [],
};

/** Value used only to faithfully display a vendor-side mode we do not own. */
export const NATIVE_PERMISSION_CUSTOM_VALUE = "__custom__";
export const NATIVE_PERMISSION_CUSTOM_OPTION: NativePermissionModeOption = {
  value: NATIVE_PERMISSION_CUSTOM_VALUE,
  label: "Custom",
  description: "Configured inside the native harness; choose another mode to replace it",
  args: [],
};

export interface NativePermissionControl {
  label: "Permissions" | "Approval" | "Mode";
  description: string;
  defaultValue: string;
  options: readonly NativePermissionModeOption[];
}

function canonicalHarness(harness: string | null | undefined): string | null {
  if (harness === "native-cursor") return "cursor-native";
  if (harness === "native-antigravity") return "antigravity-native";
  return harness ?? null;
}

/** Return the permission control supported by a native harness, if any. */
export function nativePermissionControlForHarness(
  harness: string | null | undefined,
): NativePermissionControl | null {
  switch (canonicalHarness(harness)) {
    case "claude-native":
      return {
        label: "Permissions",
        description: "What Claude can do without asking",
        defaultValue: CLAUDE_NATIVE_DEFAULT_PERMISSION_MODE,
        options: CLAUDE_NATIVE_PERMISSION_MODES,
      };
    case "codex-native":
      return {
        label: "Approval",
        description: "Approval and command sandbox policy",
        defaultValue: CODEX_NATIVE_DEFAULT_APPROVAL_MODE,
        options: [...CODEX_NATIVE_APPROVAL_MODES, CODEX_NATIVE_BYPASS_APPROVAL_OPTION],
      };
    case "cursor-native":
      return {
        label: "Mode",
        description: "How Cursor runs commands",
        defaultValue: CURSOR_NATIVE_DEFAULT_EXEC_MODE,
        options: CURSOR_NATIVE_EXEC_MODES,
      };
    case "antigravity-native":
      return {
        label: "Permissions",
        description: "Whether Antigravity asks before tools run",
        defaultValue: AGY_NATIVE_DEFAULT_SKIP_MODE,
        options: AGY_NATIVE_SKIP_MODES,
      };
    default:
      return null;
  }
}

function optionValue(args: readonly string[], names: readonly string[]): string | null {
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (names.includes(arg) && i + 1 < args.length) return args[i + 1];
    const joined = names.find((name) => arg.startsWith(`${name}=`));
    if (joined) return arg.slice(joined.length + 1);
  }
  return null;
}

function configValue(args: readonly string[], key: string): string | null {
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    let assignment: string | null = null;
    if ((arg === "-c" || arg === "--config") && i + 1 < args.length) assignment = args[i + 1];
    else if (arg.startsWith("-c=") || arg.startsWith("--config="))
      assignment = arg.slice(arg.indexOf("=") + 1);
    if (assignment?.startsWith(`${key}=`)) {
      return assignment
        .slice(key.length + 1)
        .trim()
        .replace(/^["']|["']$/g, "");
    }
  }
  return null;
}

/** Derive the selectable UI mode from a session's persisted native launch config. */
export function nativePermissionModeFromSession(
  harness: string | null | undefined,
  terminalLaunchArgs: readonly string[] | null | undefined,
  labels?: Record<string, string> | null,
): string | null {
  const canonical = canonicalHarness(harness);
  const control = nativePermissionControlForHarness(canonical);
  if (!control) return null;
  const args = terminalLaunchArgs ?? [];

  if (canonical === "claude-native") {
    const value = optionValue(args, ["--permission-mode"]);
    if (value === null) return control.defaultValue;
    return control.options.some((option) => option.value === value)
      ? value
      : NATIVE_PERMISSION_CUSTOM_VALUE;
  }
  if (canonical === "cursor-native") {
    if (args.includes("--yolo")) return "yolo";
    if (args.includes("--auto-review")) return "auto-review";
    const value = optionValue(args, ["--mode"]);
    if (value === null) return control.defaultValue;
    return value === "plan" || value === "ask" ? value : NATIVE_PERMISSION_CUSTOM_VALUE;
  }
  if (canonical === "antigravity-native") {
    return args.includes("--dangerously-skip-permissions")
      ? AGY_NATIVE_SKIP_VALUE
      : AGY_NATIVE_DEFAULT_SKIP_MODE;
  }

  if (
    labels?.[CODEX_NATIVE_BYPASS_SANDBOX_LABEL_KEY] === "1" ||
    args.includes("--dangerously-bypass-approvals-and-sandbox")
  ) {
    return CODEX_NATIVE_BYPASS_APPROVAL_VALUE;
  }
  const sandbox = optionValue(args, ["--sandbox", "-s"]) ?? configValue(args, "sandbox_mode");
  const approval =
    optionValue(args, ["--ask-for-approval", "-a"]) ?? configValue(args, "approval_policy");
  const profile = configValue(args, "default_permissions");
  if (profile === ":danger-full-access" && (approval === null || approval === "never")) {
    return "full-access";
  }
  if (profile != null && profile !== ":workspace") return NATIVE_PERMISSION_CUSTOM_VALUE;
  if (sandbox === "danger-full-access" && approval === "never") return "full-access";
  if (sandbox === "read-only") return "read-only";
  if (sandbox === null && approval === null && profile === null) return control.defaultValue;
  return NATIVE_PERMISSION_CUSTOM_VALUE;
}

function stripValueOptions(
  args: readonly string[],
  valueOptions: ReadonlySet<string>,
  flagOptions: ReadonlySet<string>,
  configKeys: ReadonlySet<string> = new Set(),
): string[] {
  const kept: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (flagOptions.has(arg)) continue;
    if (valueOptions.has(arg)) {
      if (i + 1 < args.length && !args[i + 1].startsWith("-")) i++;
      continue;
    }
    if ([...valueOptions].some((name) => arg.startsWith(`${name}=`))) continue;
    if ((arg === "-c" || arg === "--config") && i + 1 < args.length) {
      const assignment = args[i + 1];
      const configKey = assignment.split("=", 1)[0].trim();
      if (configKeys.has(configKey)) {
        i++;
        continue;
      }
      kept.push(arg, args[++i]);
      continue;
    }
    if (arg.startsWith("-c=") || arg.startsWith("--config=")) {
      const assignment = arg.slice(arg.indexOf("=") + 1);
      if (configKeys.has(assignment.split("=", 1)[0].trim())) continue;
    }
    kept.push(arg);
  }
  return kept;
}

/** Replace only permission-related argv, preserving model/provider/custom flags. */
export function mergeNativePermissionModeArgs(
  harness: string | null | undefined,
  existingArgs: readonly string[] | null | undefined,
  mode: string,
): string[] {
  if (mode === NATIVE_PERMISSION_CUSTOM_VALUE) return [...(existingArgs ?? [])];
  const canonical = canonicalHarness(harness);
  const control = nativePermissionControlForHarness(canonical);
  if (!control) return [...(existingArgs ?? [])];
  const option = control.options.find((candidate) => candidate.value === mode);
  if (!option) return [...(existingArgs ?? [])];

  let kept: string[];
  if (canonical === "claude-native") {
    kept = stripValueOptions(existingArgs ?? [], new Set(["--permission-mode"]), new Set());
  } else if (canonical === "cursor-native") {
    kept = stripValueOptions(
      existingArgs ?? [],
      new Set(["--mode"]),
      new Set(["--auto-review", "--yolo"]),
    );
  } else if (canonical === "antigravity-native") {
    kept = stripValueOptions(
      existingArgs ?? [],
      new Set(),
      new Set(["--dangerously-skip-permissions"]),
    );
  } else {
    kept = stripValueOptions(
      existingArgs ?? [],
      new Set(["--ask-for-approval", "-a", "--sandbox", "-s"]),
      new Set(["--dangerously-bypass-approvals-and-sandbox"]),
      new Set(["approval_policy", "approvals_reviewer", "default_permissions", "sandbox_mode"]),
    );
  }
  return [...kept, ...option.args];
}
