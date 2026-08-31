import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const here = dirname(fileURLToPath(import.meta.url));
const repoSrc = resolve(here, "..", "..", "..", "src");

type CallPayload = { ok: boolean; result?: unknown; message?: string };

function callDiagrun(
  op: string,
  params: Record<string, unknown>,
  signal: AbortSignal | undefined,
): Promise<CallPayload> {
  return new Promise((resolvePromise, reject) => {
    const python = process.env.DIAGRUN_PYTHON || "python3";
    const pythonpath = process.env.PYTHONPATH
      ? `${repoSrc}:${process.env.PYTHONPATH}`
      : repoSrc;
    const child = spawn(python, ["-m", "diagrun", "call", op], {
      env: { ...process.env, PYTHONPATH: pythonpath },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const abort = () => child.kill("SIGTERM");
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    }
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", () => {
      if (signal) signal.removeEventListener("abort", abort);
      try {
        resolvePromise(JSON.parse(stdout) as CallPayload);
      } catch {
        reject(new Error(stderr.trim() || stdout.trim() || "diagrun call failed"));
      }
    });
    child.stdin.write(JSON.stringify(params || {}));
    child.stdin.end();
  });
}

async function run(
  op: string,
  params: Record<string, unknown>,
  signal: AbortSignal | undefined,
) {
  const payload = await callDiagrun(op, params, signal);
  if (!payload.ok) {
    return {
      content: [{ type: "text" as const, text: payload.message || `diagrun ${op} failed` }],
      details: { ok: false },
    };
  }
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload.result) }],
    details: payload.result,
  };
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "diagrun_build",
    label: "Diagrun build",
    description:
      "Run a C++ build through diagrun. Returns compact root diagnostics the model can act on. Prefer this over bash. Call diagrun_get_raw only if roots and unclassified are both empty. If the result has no_progress: true, your last edit did not change the roots -- stop rebuilding and re-check the diff instead of calling this again.",
    promptSnippet: "diagrun_build: wrap C++ builds; act on roots; do not fetch the raw log unless roots are empty; stop retrying on no_progress",
    promptGuidelines: [
      "Use diagrun_build for C++ compile/link commands instead of bash.",
      "Read roots[].message, roots[].location, and roots[].snippet. Do not call diagrun_get_raw when roots is non-empty.",
      "If the result has no_progress: true, do not call diagrun_build again immediately -- re-read the file you just edited first.",
    ],
    parameters: Type.Object({
      command: Type.String({ description: 'Build command, e.g. "cmake --build build"' }),
      cwd: Type.Optional(Type.String({ description: "Working directory" })),
      inject_diagnostics: Type.Optional(Type.Boolean()),
      collapse_parse_recovery: Type.Optional(
        Type.Boolean({
          description:
            "Fold syntax-recovery follow-on errors into the causing root's evidence instead of listing them as separate roots (default true).",
        }),
      ),
    }),
    async execute(_id, params, signal) {
      return run("build", params as Record<string, unknown>, signal);
    },
  });

  pi.registerTool({
    name: "diagrun_get_raw",
    label: "Diagrun raw log",
    description: "Retrieve stored raw compiler/build output for a diagrun run_id.",
    parameters: Type.Object({
      run_id: Type.Optional(Type.String({ description: "Run id; omit for last run" })),
      offset: Type.Optional(Type.Number()),
      limit: Type.Optional(Type.Number()),
    }),
    async execute(_id, params, signal) {
      return run("get_raw", params as Record<string, unknown>, signal);
    },
  });

  pi.registerTool({
    name: "diagrun_show",
    label: "Diagrun show",
    description: "Return metadata for a stored diagrun run.",
    parameters: Type.Object({
      run_id: Type.Optional(Type.String()),
    }),
    async execute(_id, params, signal) {
      return run("show", params as Record<string, unknown>, signal);
    },
  });

  pi.registerTool({
    name: "diagrun_get_diagnostic",
    label: "Diagrun diagnostic",
    description: "Return one parsed diagnostic from a run when available.",
    parameters: Type.Object({
      run_id: Type.String(),
      diagnostic_id: Type.String(),
    }),
    async execute(_id, params, signal) {
      return run("get_diagnostic", params as Record<string, unknown>, signal);
    },
  });
}
