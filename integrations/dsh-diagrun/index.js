import { defineTool } from '@deepseek-ai/dsh-tools'
import { callDiagrun } from './call.mjs'

export const name = 'dsh-diagrun'
export const inject = ['tools']

function renderJson(_args, value) {
  return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
}

const objectOut = {
  schema: { type: 'object', additionalProperties: true },
  render: renderJson,
}

async function run(op, args, exec) {
  const payload = await callDiagrun(op, args, exec && exec.signal)
  if (!payload.ok) {
    const err = new Error(payload.message || `diagrun ${op} failed`)
    throw err
  }
  return payload.result
}

export function apply(ctx) {
  ctx.tools.register(
    defineTool({
      name: 'diagrun_build',
      description:
        'Run a C++ build through diagrun and return compact status plus a raw-log reference. Prefer this over bash/make for failed C++ builds.',
      parameters: {
        command: {
          type: 'string',
          required: true,
          description: 'Build command, e.g. "cmake --build build" or "make -j8".',
        },
        cwd: { type: 'string', description: 'Working directory.' },
        inject_diagnostics: {
          type: 'boolean',
          description: 'Inject -fdiagnostics-format=json (default true).',
        },
        collapse_parse_recovery: {
          type: 'boolean',
          description: 'Hide likely syntax-recovery follow-on errors (default false).',
        },
      },
      output: objectOut,
      async execute(args, exec) {
        return run('build', args, exec)
      },
      presentCall(args) {
        return { card: 'terminal', title: args.command, cwd: args.cwd }
      },
    }),
  )
  ctx.tools.register(
    defineTool({
      name: 'diagrun_get_raw',
      description: 'Retrieve stored raw compiler/build output for a diagrun run_id.',
      parameters: {
        run_id: { type: 'string', description: 'Run id; omit to use the last run.' },
        offset: { type: 'number', description: 'Byte offset.' },
        limit: { type: 'number', description: 'Max bytes to return.' },
      },
      output: objectOut,
      async execute(args, exec) {
        return run('get_raw', args, exec)
      },
    }),
  )
  ctx.tools.register(
    defineTool({
      name: 'diagrun_show',
      description: 'Return metadata for a stored diagrun run.',
      parameters: {
        run_id: { type: 'string', description: 'Run id; omit to use the last run.' },
      },
      output: objectOut,
      async execute(args, exec) {
        return run('show', args, exec)
      },
    }),
  )
  ctx.tools.register(
    defineTool({
      name: 'diagrun_get_diagnostic',
      description: 'Return one parsed diagnostic from a run when diagnostics.json exists.',
      parameters: {
        run_id: { type: 'string', required: true },
        diagnostic_id: { type: 'string', required: true },
      },
      output: objectOut,
      async execute(args, exec) {
        return run('get_diagnostic', args, exec)
      },
    }),
  )
}
