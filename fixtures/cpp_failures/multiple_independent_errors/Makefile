CXX ?= g++
CXXFLAGS ?= -fdiagnostics-color=never -fno-diagnostics-show-caret -std=c++17

.PHONY: all one.o two.o
all: one.o two.o

one.o:
	$(CXX) $(CXXFLAGS) -c src/one.cpp -o one.o

two.o:
	$(CXX) $(CXXFLAGS) -c src/two.cpp -o two.o
