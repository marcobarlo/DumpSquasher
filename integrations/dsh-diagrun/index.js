import { callDiagrun } from './call.mjs'
import { bashForegroundResult, isBuildCommand } from './wrap.mjs'

export const name = 'dsh-diagrun'
export const inject = ['tools', 'systemPrompt']

const PROMPT =
  'C++ builds invoked through bash (`make`, `ninja`, `cmake --build`, `g++`/`clang++`) ' +
  'return compact root diagnostics (file/line/message/snippet) instead of the compiler dump. ' +
  'Act on roots. If no_progress is true, stop rebuilding and inspect the source. ' +
  'The full log stays on disk; do not fetch it unless roots and unclassified are both empty.'

function resolveCwd(exec, args) {
  if (typeof args.workdir === 'string' && args.workdir.trim()) return args.workdir
  const cwd = exec.agent?.session?.header?.cwd
  return typeof cwd === 'string' && cwd.trim() ? cwd : undefined
}

export function apply(ctx) {
  ctx.systemPrompt.section({
    name: 'tool:bash:diagrun',
    order: 106,
    text: PROMPT,
  })
  ctx.on('tools/execute', async (exec, next) => {
    if (exec.name !== 'bash') return next()
    const args = exec.arguments || {}
    if (args.run_in_background === true) return next()
    const command = String(args.command || '')
    if (!isBuildCommand(command)) return next()
    const cwd = resolveCwd(exec, args)
    const timeoutMs = typeof args.timeoutMs === 'number' ? args.timeoutMs : 600000
    try {
      const payload = await callDiagrun(
        'build',
        {
          command,
          ...(cwd ? { cwd } : {}),
          collapse_parse_recovery: true,
        },
        exec.signal,
      )
      if (!payload.ok) return next()
      const result = payload.result || {}
      const exitCode = Number(result.exit_code)
      return {
        value: bashForegroundResult(
          JSON.stringify(result),
          Number.isFinite(exitCode) ? exitCode : 1,
          timeoutMs,
        ),
      }
    } catch {
      return next()
    }
  })
}
