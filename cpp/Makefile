CXX := g++
CXXFLAGS := -O3 -std=c++20 -march=native -ffast-math -Wall -Wextra -pedantic
TARGET := option_pricer
SRC := main.cpp

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC)

clean:
	rm -f $(TARGET)
