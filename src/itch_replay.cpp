#include "jitter/itch.hpp"
#include "jitter/itch_window.hpp"

#include <nlohmann/json.hpp>
#include <sys/resource.h>

#include <algorithm>
#include <bitset>
#include <charconv>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace {
using namespace jitter::itch;
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

constexpr std::size_t max_locates = 1u << 16;

json quote_json(Top top) {
    const auto level = [](Quote quote) { return json::array({quote.price, quote.size}); };
    return {{"bid", level(top[0])}, {"ask", level(top[1])}};
}

json order_json(const Orders::value_type& entry) {
    const auto& [id, order] = entry;
    return {{"id", id}, {"symbol_locate", order.locate}, {"price", order.price},
            {"size", order.quantity}, {"side", order.side == Side::buy ? "buy" : "sell"}};
}

std::uint64_t rss_bytes() {
    struct rusage usage {};
    if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
#if defined(__APPLE__)
    return usage.ru_maxrss;
#else
    return static_cast<std::uint64_t>(usage.ru_maxrss) * 1024;
#endif
}

json replay(bool verify, std::string_view filter, Sequence limit, std::vector<Window>& windows) {
    Book book;
    Reference reference;
    TapeReader reader(stdin);
    std::array<Sequence, 256> types{};
    std::bitset<max_locates> updated;
    std::vector<std::string> directory(max_locates);
    std::size_t symbols = 0, peak_orders = 0;
    Sequence updates = 0, quote_checks = 0;
    json checkpoints = json::array();
    std::string milestones;
    bool requested_symbol_seen = false;
    const auto start = Clock::now();
    const auto checkpoint = [&](char event, Sequence sequence, std::uint64_t market_ns) {
        const auto candidate = book.orders();
        if (verify) {
            const auto expected = reference.orders();
            const auto [actual_row, expected_row] = std::mismatch(candidate.begin(), candidate.end(),
                                                                   expected.begin(), expected.end());
            if (actual_row != candidate.end() || expected_row != expected.end()) {
                const json mismatch = {
                    {"actual", actual_row == candidate.end() ? json(nullptr) : order_json(*actual_row)},
                    {"expected", expected_row == expected.end() ? json(nullptr) : order_json(*expected_row)}};
                throw std::runtime_error("checkpoint state mismatch: " + mismatch.dump());
            }
        }
        checkpoints.push_back({{"event", std::string(1, event)}, {"sequence", sequence},
                               {"market_ns", market_ns}, {"active_orders", candidate.size()},
                               {"book_fingerprint", fingerprint(candidate)}});
    };

    while ((!limit || reader.sequence() < limit) && reader.next()) {
        try {
            const auto message = decode(reader.bytes());
            ++types[static_cast<unsigned char>(message.type)];
            auto& name = directory[message.locate];
            if (message.type == 'R') {
                const auto symbol = symbol_name(message);
                if (!message.locate || symbol.empty() || (!name.empty() && name != symbol))
                    throw std::runtime_error("invalid stock directory entry");
                if (name.empty()) ++symbols;
                name = symbol;
                requested_symbol_seen |= symbol == filter;
            }
            if (message.type == 'S') {
                milestones += message.event;
                if (message.event == 'Q' || message.event == 'M' || message.event == 'C')
                    checkpoint(message.event, reader.sequence(), message.time);
            }
            for (auto& window : windows) window.before(reader.sequence(), message, reference);
            if (message.updates_book() && (filter.empty() || name == filter)) {
                if (name.empty()) throw std::runtime_error("book update precedes stock directory entry");
                if (!book.apply(message))
                    throw std::runtime_error("invalid order lifecycle for " + name + " ref " +
                                             std::to_string(message.order) + " type " + message.type);
                ++updates;
                updated.set(message.locate);
                peak_orders = std::max(peak_orders, book.size());
                if (verify) {
                    if (!reference.apply(message))
                        throw std::runtime_error("independent reference rejected order update for " + name);
                    const auto actual = book.top(message.locate);
                    const auto expected = reference.top(message.locate);
                    if (actual != expected)
                        throw std::runtime_error("top of book mismatch for " + name + ": " +
                            json{{"actual", quote_json(actual)}, {"expected", quote_json(expected)}}.dump());
                    ++quote_checks;
                }
            }
            for (auto& window : windows) window.after(reader.sequence(), message, reader.bytes(), reference);
        } catch (const std::exception& error) {
            throw std::runtime_error("sequence " + std::to_string(reader.sequence()) + ": " + error.what());
        }
    }
    if (!reader.sequence()) throw std::runtime_error("empty historical tape");
    if (!filter.empty() && !requested_symbol_seen)
        throw std::runtime_error("requested symbol is absent from the stock directory");
    if (checkpoints.empty() || checkpoints.back().at("sequence") != reader.sequence())
        checkpoint('-', reader.sequence(), 0);
    json type_counts = json::object();
    for (unsigned index = 0; index < types.size(); ++index)
        if (types[index]) type_counts[std::string(1, static_cast<char>(index))] = types[index];
    json captured = json::object();
    for (const auto& window : windows) {
        if (!window.complete()) throw std::runtime_error("incomplete capture: " + window.name);
        captured[window.name] = {{"first_sequence", window.first}, {"messages", window.count},
                                 {"first_market_ns", window.first_market_ns}, {"last_market_ns", window.last_market_ns}};
    }
    const auto seconds = std::chrono::duration<double>(Clock::now() - start).count();
    return {{"schema", 1}, {"format", "nasdaq-totalview-itch-5.0"},
            {"scope", filter.empty() ? json("all-symbols") : json(filter)},
            {"complete_session", milestones.starts_with('O') && milestones.ends_with('C') &&
                                 milestones.find("Q") != std::string::npos && milestones.find("M") != std::string::npos},
            {"system_events", milestones}, {"messages", reader.sequence()},
            {"directory_symbols", symbols}, {"updated_symbols", updated.count()}, {"book_updates", updates},
            {"message_types", type_counts}, {"reference_quote_checks", quote_checks},
            {"reference_state_checked", verify}, {"checkpoints", checkpoints}, {"windows", captured},
            {"peak_active_orders", peak_orders}, {"peak_rss_bytes", rss_bytes()},
            {"wall_seconds", seconds}, {"messages_per_second", reader.sequence() / seconds}};
}
}

int main(int argc, char** argv) {
    try {
        bool verify = false;
        std::string_view filter;
        Sequence limit{};
        std::filesystem::path capture;
        Sequence window_count = 40000;
        for (int i = 1; i < argc; ++i) {
            const std::string_view arg = argv[i];
            if (arg == "--verify") verify = true;
            else if (arg == "--symbol" && i + 1 < argc) filter = argv[++i];
            else if (arg == "--capture" && i + 1 < argc) capture = argv[++i];
            else if ((arg == "--limit" || arg == "--window-messages") && i + 1 < argc) {
                const std::string_view value = argv[++i];
                auto& count = arg == "--limit" ? limit : window_count;
                const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), count);
                if (error != std::errc{} || end != value.data() + value.size() || !count)
                    throw std::runtime_error(std::string(arg) + " requires a positive count");
            }
            else if (arg == "--help") {
                std::cout << "usage: jitter_itch [--verify] [--symbol TICKER] [--limit N]"
                             " [--capture NEW_DIRECTORY --window-messages N] < historical_binary_file\n";
                return 0;
            } else throw std::runtime_error("invalid option: " + std::string(arg));
        }
        if (std::setvbuf(stdin, nullptr, _IOFBF, 1u << 20))
            throw std::runtime_error("cannot configure tape buffer");
        std::vector<Window> windows;
        if (!capture.empty()) {
            if (!verify || !filter.empty() || window_count <= recovery_offset || window_count > 1000000)
                throw std::runtime_error("capture requires all-symbol --verify and 97..1000000 messages per window");
            if (!std::filesystem::create_directories(capture))
                throw std::runtime_error("capture directory already exists");
            windows.push_back({"open", 34200000000000ull, 0, 0, 0, 0, window_count, capture / "open", {}});
            windows.push_back({"quiet", 43200000000000ull, 0, 0, 0, 0, window_count, capture / "quiet", {}});
        }
        std::cout << replay(verify, filter, limit, windows).dump(2) << '\n';
    } catch (const std::exception& error) {
        std::cerr << "jitter_itch: " << error.what() << '\n';
        return 1;
    }
}
