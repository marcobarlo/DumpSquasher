#!/usr/bin/env python3
"""Write a failing C++ tree whose `make -k` dump is large.

Every translation unit uses Widget::foo, which is missing from the header.
Default: 64 TUs + main → tens of KB of repeated GCC JSON, one independent root.
"""

from __future__ import annotations

import argparse
from pathlib import Path

HEADER = """\
struct Widget {
    int bar;
};
"""

MAIN = """\
#include "widget.hpp"

int main() {
    Widget w;
    return w.foo;
}
"""

TU = """\
#include "widget.hpp"

int use_{i}() {{
    Widget w;
    return w.foo;
}}
"""

MAKEFILE = """\
CXX ?= g++
CXXFLAGS ?= -fdiagnostics-color=never -fno-diagnostics-show-caret -std=c++17 -Iinclude
# Keep going so every TU contributes to the dump (fat log).
MAKEFLAGS += -k

SRCS := $(wildcard src/*.cpp)
OBJS := $(patsubst src/%.cpp,build/%.o,$(SRCS))

.PHONY: all run
all: app

build/%.o: src/%.cpp include/widget.hpp
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -c $< -o $@

app: $(OBJS)
	$(CXX) $(OBJS) -o app

run: app
	./app
"""


def write_app(dest: Path, n_tus: int) -> None:
    dest = dest.resolve()
    (dest / "include").mkdir(parents=True, exist_ok=True)
    (dest / "src").mkdir(parents=True, exist_ok=True)
    (dest / "include" / "widget.hpp").write_text(HEADER, encoding="utf-8")
    (dest / "src" / "main.cpp").write_text(MAIN, encoding="utf-8")
    for i in range(n_tus):
        (dest / "src" / f"tu_{i:02d}.cpp").write_text(TU.format(i=i), encoding="utf-8")
    (dest / "Makefile").write_text(MAKEFILE, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--tus", type=int, default=64, help="extra TUs besides main.cpp")
    args = parser.parse_args()
    write_app(args.dest, args.tus)
    print(f"wrote {args.dest} with {args.tus + 1} translation units")


if __name__ == "__main__":
    main()
