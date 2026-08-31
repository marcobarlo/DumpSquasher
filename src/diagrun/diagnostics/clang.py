"""Clang diagnostic parser (JSON + ``file:line:col:`` text)."""

from diagrun.diagnostics.compiler import parse_compiler_log

parse_clang_log = parse_compiler_log
