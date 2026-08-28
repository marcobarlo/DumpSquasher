#include <concepts>

template <std::integral T>
T twice(T value) {
    return value + value;
}

int main() { return twice(1.5); }
