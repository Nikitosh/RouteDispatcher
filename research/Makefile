SRC := $(wildcard solvers/s*.cpp)
BIN := $(patsubst solvers/%.cpp,bin/%,$(SRC))
all: $(BIN)
bin/%: solvers/%.cpp $(wildcard solvers/*.hpp)
	clang++ -std=c++17 -O2 -march=native -o $@ $<
