CXX ?= g++
CXXFLAGS ?= -fdiagnostics-color=never -fno-diagnostics-show-caret -std=c++17 -Iinclude

.PHONY: all foo.o bar.o baz.o
all: foo.o bar.o baz.o

foo.o:
	$(CXX) $(CXXFLAGS) -c src/foo.cpp -o foo.o

bar.o:
	$(CXX) $(CXXFLAGS) -c src/bar.cpp -o bar.o

baz.o:
	$(CXX) $(CXXFLAGS) -c src/baz.cpp -o baz.o
