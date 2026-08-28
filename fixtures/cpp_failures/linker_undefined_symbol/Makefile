CXX ?= g++
CXXFLAGS ?= -fdiagnostics-color=never -fno-diagnostics-show-caret -std=c++17

.PHONY: all
all:
	$(CXX) $(CXXFLAGS) -c src/main.cpp -o main.o
	$(CXX) main.o -o main

