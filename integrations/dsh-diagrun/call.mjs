import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const DIAGRUN_SRC = resolve(here, '..', '..', 'src')

/**
 * Invoke `diagrun call OP` with JSON params. Used by the DeepSeek Harness plugin.
 * @param {string} op
 * @param {Record<string, unknown>} params
 * @param {AbortSignal} [signal]
 */
export function callDiagrun(op, params, signal) {
  return new Promise((resolvePromise, reject) => {
    const python = process.env.DIAGRUN_PYTHON || 'python3'
    const pythonpath = process.env.PYTHONPATH
      ? `${DIAGRUN_SRC}:${process.env.PYTHONPATH}`
      : DIAGRUN_SRC
    const child = spawn(python, ['-m', 'diagrun', 'call', op], {
      env: { ...process.env, PYTHONPATH: pythonpath },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    const abort = () => child.kill('SIGTERM')
    if (signal) {
      if (signal.aborted) {
        abort()
      } else {
        signal.addEventListener('abort', abort, { once: true })
      }
    }
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', reject)
    child.on('close', (code) => {
      if (signal) signal.removeEventListener('abort', abort)
      try {
        const parsed = JSON.parse(stdout)
        resolvePromise(parsed)
      } catch (err) {
        reject(new Error(stderr.trim() || stdout.trim() || `diagrun call exited ${code}`))
      }
    })
    child.stdin.write(JSON.stringify(params || {}))
    child.stdin.end()
  })
}
