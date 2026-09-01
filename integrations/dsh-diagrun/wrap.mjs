/**
 * Decide whether a bash command is a C++ compile/link invocation that
 * diagrun should run instead of the raw compiler dump.
 */

const MAKE_SKIP = new Set([
  "clean",
  "distclean",
  "mostlyclean",
  "test",
  "check",
  "install",
  "uninstall",
  "tags",
  "help",
])

const MAKE_TAKES_ARG = new Set([
  "-C",
  "-f",
  "-I",
  "--directory",
  "--file",
  "--makefile",
  "--include-dir",
])

const MAKE_OPTIONAL_NUMERIC = new Set(["-j", "--jobs"])

const COMPILER = /(?:g\+\+|clang\+\+|c\+\+)$/

/**
 * True when `command` is a single compile/link invocation (no shell combinators).
 * @param {string} command
 * @returns {boolean}
 */
export function isBuildCommand(command) {
  const text = String(command || "").trim()
  if (!text) return false
  const stripped = text.replace(/(?:\d*)>&?\d+/g, " ").replace(/\s+/g, " ").trim()
  if (!stripped) return false
  if (/[|;\n`<>]|\$\(|&&|\|\|/.test(stripped)) return false
  const parts = tokenize(stripped)
  if (!parts.length) return false
  const bin = basename(parts[0])
  if (bin === "make" || bin === "gmake") {
    const goals = makeGoals(parts)
    return !goals.some((g) => MAKE_SKIP.has(g))
  }
  if (bin === "ninja") {
    return !parts.slice(1).includes("-t")
  }
  if (bin === "cmake") {
    return parts.includes("--build")
  }
  return COMPILER.test(bin)
}

/**
 * @param {string} text
 * @returns {string[]}
 */
export function tokenize(text) {
  const parts = text.trim().split(/\s+/).filter(Boolean)
  while (parts.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(parts[0])) {
    parts.shift()
  }
  return parts
}

/**
 * @param {string} path
 * @returns {string}
 */
function basename(path) {
  const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
  return i >= 0 ? path.slice(i + 1) : path
}

/**
 * @param {string[]} parts
 * @returns {string[]}
 */
function makeGoals(parts) {
  const goals = []
  for (let i = 1; i < parts.length; i += 1) {
    const tok = parts[i]
    if (MAKE_OPTIONAL_NUMERIC.has(tok)) {
      if (i + 1 < parts.length && /^\d+$/.test(parts[i + 1])) i += 1
      continue
    }
    if (MAKE_TAKES_ARG.has(tok)) {
      i += 1
      continue
    }
    if (/^-[CIj].+/.test(tok) && tok.length > 2) continue
    if (tok.startsWith("-")) continue
    goals.push(tok)
  }
  return goals
}

/**
 * Bash-tool output value so DSH render() still emits `[exit code: N]`.
 * @param {string} text
 * @param {number} exitCode
 * @param {number} [timeoutMs]
 */
export function bashForegroundResult(text, exitCode, timeoutMs = 600000) {
  return {
    kind: "foreground",
    exitCode,
    signal: null,
    timedOut: false,
    aborted: false,
    timeoutMs,
    stdout: { text, truncated: false },
    stderr: { text: "", truncated: false },
  }
}
