template <typename T>
T identity(T value) {
    return value.missing;
}

int main() { return identity(1); }
