#pragma once
#include <cstdint>
#include <memory>

namespace jitter {
struct ApplicationConfig;
// One immutable in-memory database and one statement, serialized by the
// caller's traced mutex. No external SQL, extension, file, or command input.
class SqliteDataset {
public:
    explicit SqliteDataset(const ApplicationConfig& config);
    ~SqliteDataset();
    std::uint64_t aggregate(std::uint32_t key, std::uint32_t rows);
private:
    struct State;
    std::unique_ptr<State> state_;
};
}
