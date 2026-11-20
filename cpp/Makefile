CXX := g++
CXXFLAGS := -O3 -std=c++20 -Wall -Wextra -pedantic
TARGET := option_pricer
LIBRARY := liboption_pricer.so
SRC := main.cpp

all: $(TARGET) $(LIBRARY)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC)

$(LIBRARY): $(SRC)
	$(CXX) $(CXXFLAGS) -fPIC -shared -o $@ $(SRC)

clean:
	rm -f $(TARGET) $(LIBRARY)
