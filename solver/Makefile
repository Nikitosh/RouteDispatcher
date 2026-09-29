# Решатели продукта: plan — план на день (кооперативный параллельный поиск), baseline — базовый вариант из ТЗ,
# fleet_bound — нижняя граница числа бригад (генерация столбцов).
CXX ?= c++
# Флаги намеренно не берутся из окружения (CXXFLAGS): сборка должна быть одинаковой везде.
FLAGS := -std=c++17 -O2 -march=native -Wall -Wextra -Wno-unused-parameter
HEADERS := $(shell find src -name '*.hpp')

all: bin/plan bin/baseline bin/fleet_bound

bin/%: src/%.cpp $(HEADERS)
	@mkdir -p bin
	$(CXX) $(FLAGS) -Isrc -pthread -o $@ $<

clean:
	rm -rf bin

.PHONY: all clean
