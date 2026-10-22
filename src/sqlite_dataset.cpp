#include "jitter/sqlite_dataset.hpp"
#include "jitter/application.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <stdexcept>
#include <string>

namespace jitter {
namespace {
void require_sql(int actual, int expected, sqlite3* db) {
    if (actual != expected) throw std::runtime_error(std::string("SQLite: ") + sqlite3_errmsg(db));
}
using Connection = std::unique_ptr<sqlite3, decltype(&sqlite3_close)>;
using Statement = std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)>;
Statement prepare(sqlite3* db, const char* sql) {
    sqlite3_stmt* raw{};
    const auto status = sqlite3_prepare_v2(db, sql, -1, &raw, nullptr);
    Statement statement(raw, sqlite3_finalize);
    require_sql(status, SQLITE_OK, db);
    return statement;
}
void execute(sqlite3* db, const char* sql) {
    require_sql(sqlite3_exec(db, sql, nullptr, nullptr, nullptr), SQLITE_OK, db);
}
}
struct SqliteDataset::State {
    Connection db{nullptr, sqlite3_close};
    Statement query{nullptr, sqlite3_finalize};
};

SqliteDataset::SqliteDataset(const ApplicationConfig& config) : state_(std::make_unique<State>()) {
    const auto maximum = std::max(config.light_rows, config.heavy_rows);
    if (!config.light_rows || !config.heavy_rows || maximum > 4096 ||
        static_cast<std::uint64_t>(config.key_count) * maximum > 1000000) {
        throw std::invalid_argument("SQLite rows must be 1..4096 and total prepared rows at most one million");
    }
    sqlite3* raw{};
    const auto status = sqlite3_open_v2(":memory:", &raw, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nullptr);
    state_->db.reset(raw);
    require_sql(status, SQLITE_OK, raw);
    execute(raw, "PRAGMA temp_store=MEMORY; PRAGMA automatic_index=OFF; CREATE TABLE items(bucket INTEGER NOT NULL, seq INTEGER NOT NULL, value INTEGER NOT NULL); BEGIN;");
    {
        auto insert = prepare(raw, "INSERT INTO items VALUES(?1,?2,?3)");
        constexpr std::uint64_t golden = UINT64_C(0x9e3779b97f4a7c15);
        for (std::uint32_t key=0; key<config.key_count; ++key) {
            for (std::uint32_t row=0; row<maximum; ++row) {
                const auto value = mix64(config.dataset_seed + golden*(static_cast<std::uint64_t>(key)+1) + golden*(static_cast<std::uint64_t>(row)+1)) % 1000000;
                require_sql(sqlite3_bind_int64(insert.get(), 1, key), SQLITE_OK, raw);
                require_sql(sqlite3_bind_int64(insert.get(), 2, row), SQLITE_OK, raw);
                require_sql(sqlite3_bind_int64(insert.get(), 3, static_cast<sqlite3_int64>(value)), SQLITE_OK, raw);
                require_sql(sqlite3_step(insert.get()), SQLITE_DONE, raw);
                require_sql(sqlite3_reset(insert.get()), SQLITE_OK, raw);
            }
        }
    }
    execute(raw, "COMMIT;");
    if (config.implementation == "indexed") execute(raw, "CREATE INDEX items_lookup ON items(bucket,seq);");
    execute(raw, "PRAGMA query_only=ON;");
    state_->query = prepare(raw, "SELECT COALESCE(SUM((seq+1)*value),0) FROM items WHERE bucket=?1 AND seq<?2");
}
SqliteDataset::~SqliteDataset() = default;
std::uint64_t SqliteDataset::aggregate(std::uint32_t key, std::uint32_t rows) {
    auto* db = state_->db.get();
    auto* query = state_->query.get();
    require_sql(sqlite3_reset(query), SQLITE_OK, db);
    require_sql(sqlite3_bind_int64(query, 1, key), SQLITE_OK, db);
    require_sql(sqlite3_bind_int64(query, 2, rows), SQLITE_OK, db);
    require_sql(sqlite3_step(query), SQLITE_ROW, db);
    if (sqlite3_column_type(query, 0) != SQLITE_INTEGER || sqlite3_column_int64(query,0) < 0) {
        throw std::runtime_error("SQLite aggregate violated exact integer output contract");
    }
    const auto result = static_cast<std::uint64_t>(sqlite3_column_int64(query,0));
    require_sql(sqlite3_step(query), SQLITE_DONE, db);
    return result;
}
}
