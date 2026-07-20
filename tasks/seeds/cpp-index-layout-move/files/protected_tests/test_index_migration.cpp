#include "index_store.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

bool trace_migration_syscalls = false;

std::vector<std::string>& migration_syscalls() {
    static std::vector<std::string> events;
    return events;
}

std::string filename_of(const char* path) {
    const std::string value(path);
    const std::size_t separator = value.find_last_of('/');
    return separator == std::string::npos ? value : value.substr(separator + 1);
}

}  // namespace

extern "C" int __real_fsync(int descriptor);
extern "C" int __real_rename(const char* old_path, const char* new_path);

extern "C" int __wrap_fsync(int descriptor) {
    const int result = __real_fsync(descriptor);
    struct stat metadata {};
    if (result == 0 && trace_migration_syscalls &&
        ::fstat(descriptor, &metadata) == 0 && S_ISDIR(metadata.st_mode)) {
        migration_syscalls().push_back("sync-directory");
    }
    return result;
}

extern "C" int __wrap_rename(const char* old_path, const char* new_path) {
    const int result = __real_rename(old_path, new_path);
    if (result == 0 && trace_migration_syscalls) {
        migration_syscalls().push_back("rename:" + filename_of(new_path));
    }
    return result;
}

namespace {

namespace fs = std::filesystem;
using file_index::CrashPoint;
using file_index::Entry;

class Failure : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            throw Failure(std::string("CHECK failed at line ") +              \
                          std::to_string(__LINE__) + ": " #condition);         \
        }                                                                       \
    } while (false)

template <typename Exception, typename Callable>
void check_throws(Callable&& callable, int line) {
    try {
        callable();
    } catch (const Exception&) {
        return;
    } catch (const std::exception& error) {
        throw Failure("wrong exception at line " + std::to_string(line) +
                      ": " + error.what());
    }
    throw Failure("expected exception at line " + std::to_string(line));
}

#define CHECK_THROWS(exception, expression)                                    \
    check_throws<exception>([&] { (void)(expression); }, __LINE__)

fs::path fresh_directory(const std::string& name) {
    const fs::path path = fs::path("test-tmp") / name;
    fs::remove_all(path);
    fs::create_directories(path);
    return path;
}

std::vector<Entry> sample_entries() {
    return {{"zulu", "last"},
            {"alpha", "duplicate-one"},
            {std::string(1, static_cast<char>(0xff)), "unsigned-last"},
            {"alpha", "duplicate-two"},
            {"middle", std::string("binary\0value", 12)},
            {std::string(1, static_cast<char>(0x80)), "unsigned-middle"}};
}

std::vector<Entry> expected_entries() {
    return {{"alpha", "duplicate-one"},
            {"alpha", "duplicate-two"},
            {"middle", std::string("binary\0value", 12)},
            {"zulu", "last"},
            {std::string(1, static_cast<char>(0x80)), "unsigned-middle"},
            {std::string(1, static_cast<char>(0xff)), "unsigned-last"}};
}

void write_bytes(const fs::path& path, const std::vector<unsigned char>& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    CHECK(output.good());
    output.write(reinterpret_cast<const char*>(bytes.data()),
                 static_cast<std::streamsize>(bytes.size()));
    CHECK(output.good());
}

void write_text(const fs::path& path, const std::string& text) {
    write_bytes(path, std::vector<unsigned char>(text.begin(), text.end()));
}

std::vector<unsigned char> bytes_of(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    CHECK(input.good());
    return {std::istreambuf_iterator<char>(input),
            std::istreambuf_iterator<char>()};
}

void check_no_shadow_files(const fs::path& directory) {
    for (const fs::directory_entry& entry : fs::directory_iterator(directory)) {
        CHECK(entry.path().extension() != ".shadow");
    }
}

std::uint64_t checksum(const std::vector<unsigned char>& bytes) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (const unsigned char byte : bytes) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string checksum_text(std::uint64_t value) {
    std::ostringstream output;
    output << std::hex << std::setfill('0') << std::setw(16) << value;
    return output.str();
}

void replace_manifest_field(const fs::path& path, const std::string& field,
                            const std::string& value) {
    const std::vector<unsigned char> bytes = bytes_of(path);
    std::string text(bytes.begin(), bytes.end());
    const std::string marker = "\n" + field + " ";
    const std::size_t marker_position = text.find(marker);
    CHECK(marker_position != std::string::npos);
    const std::size_t value_position = marker_position + marker.size();
    const std::size_t line_end = text.find('\n', value_position);
    CHECK(line_end != std::string::npos);
    text.replace(value_position, line_end - value_position, value);
    write_text(path, text);
}

std::size_t event_position(const std::string& event, std::size_t begin = 0) {
    const std::vector<std::string>& events = migration_syscalls();
    const auto match = std::find(events.begin() + static_cast<std::ptrdiff_t>(begin),
                                 events.end(), event);
    return match == events.end()
               ? events.size()
               : static_cast<std::size_t>(std::distance(events.begin(), match));
}

void flip_byte(const fs::path& path, std::streamoff offset) {
    std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
    CHECK(file.good());
    file.seekg(offset);
    char value = 0;
    file.get(value);
    CHECK(file.good());
    value ^= 0x01;
    file.seekp(offset);
    file.put(value);
    file.flush();
    CHECK(file.good());
}

void test_complete_migration_contract() {
    const fs::path directory = fresh_directory("complete");
    const std::vector<Entry> original = sample_entries();
    file_index::create_legacy(directory, original);
    const std::vector<unsigned char> legacy_bytes = bytes_of(directory / "index.v1");

    write_text(directory / "index.data.000001.shadow", "stale data");
    write_text(directory / "index.offsets.000001.shadow", "stale offsets");
    write_text(directory / "CURRENT.shadow", "stale manifest");

    CHECK(file_index::read_legacy(directory) == original);
    file_index::migrate(directory);
    CHECK(file_index::migration_committed(directory));
    CHECK(file_index::read_current(directory) == expected_entries());
    CHECK_THROWS(file_index::OldReaderError,
                 file_index::read_legacy(directory));
    CHECK(bytes_of(directory / "index.v1") == legacy_bytes);

    const std::vector<unsigned char> current_before = bytes_of(directory / "CURRENT");
    const std::vector<unsigned char> data_before =
        bytes_of(directory / "index.data.000001");
    const std::vector<unsigned char> offsets_before =
        bytes_of(directory / "index.offsets.000001");
    file_index::migrate(directory);
    CHECK(bytes_of(directory / "CURRENT") == current_before);
    CHECK(bytes_of(directory / "index.data.000001") == data_before);
    CHECK(bytes_of(directory / "index.offsets.000001") == offsets_before);

    check_no_shadow_files(directory);
}

void test_stable_duplicate_ordering() {
    const fs::path directory = fresh_directory("stable-duplicates");
    std::vector<Entry> original;
    std::vector<Entry> expected = {{"a", "sorts-first"}};
    for (int index = 0; index < 64; ++index) {
        const Entry duplicate = {"same", "insertion-" + std::to_string(index)};
        original.push_back(duplicate);
        expected.push_back(duplicate);
        if (index == 20) {
            original.push_back({"z", "sorts-last"});
        } else if (index == 40) {
            original.push_back({"a", "sorts-first"});
        }
    }
    expected.push_back({"z", "sorts-last"});

    file_index::create_legacy(directory, original);
    file_index::migrate(directory);
    CHECK(file_index::read_current(directory) == expected);
}

void test_commit_syscall_order() {
    const fs::path directory = fresh_directory("commit-syscalls");
    file_index::create_legacy(directory, sample_entries());

    migration_syscalls().clear();
    trace_migration_syscalls = true;
    try {
        file_index::migrate(directory);
    } catch (...) {
        trace_migration_syscalls = false;
        throw;
    }
    trace_migration_syscalls = false;

    const std::size_t data_rename =
        event_position("rename:index.data.000001");
    const std::size_t offsets_rename =
        event_position("rename:index.offsets.000001");
    const std::size_t manifest_rename = event_position("rename:CURRENT");
    const std::size_t event_count = migration_syscalls().size();
    CHECK(data_rename < event_count);
    CHECK(offsets_rename < event_count);
    CHECK(manifest_rename < event_count);
    CHECK(data_rename < manifest_rename);
    CHECK(offsets_rename < manifest_rename);

    const std::size_t generation_sync =
        event_position("sync-directory", std::max(data_rename, offsets_rename) + 1);
    CHECK(generation_sync < manifest_rename);
}

void test_every_crash_has_one_readable_layout_and_retries() {
    const std::vector<CrashPoint> before_commit = {
        CrashPoint::AfterDataShadow,
        CrashPoint::AfterOffsetsShadow,
        CrashPoint::AfterManifestShadow,
        CrashPoint::AfterDataRename,
        CrashPoint::AfterOffsetsRename,
    };

    int case_number = 0;
    for (const CrashPoint point : before_commit) {
        const fs::path directory =
            fresh_directory("before-commit-" + std::to_string(case_number++));
        const std::vector<Entry> original = sample_entries();
        file_index::create_legacy(directory, original);
        const std::vector<unsigned char> legacy_bytes = bytes_of(directory / "index.v1");

        CHECK_THROWS(file_index::SimulatedCrash,
                     file_index::migrate(directory, point));
        CHECK(!file_index::migration_committed(directory));
        CHECK(file_index::read_legacy(directory) == original);
        CHECK(bytes_of(directory / "index.v1") == legacy_bytes);
        CHECK_THROWS(file_index::NoCurrentIndex,
                     file_index::read_current(directory));

        file_index::migrate(directory);
        CHECK(file_index::read_current(directory) == expected_entries());
        CHECK(bytes_of(directory / "index.v1") == legacy_bytes);
        check_no_shadow_files(directory);
    }

    const fs::path committed = fresh_directory("after-commit");
    file_index::create_legacy(committed, sample_entries());
    CHECK_THROWS(file_index::SimulatedCrash,
                 file_index::migrate(committed, CrashPoint::AfterManifestRename));
    CHECK(file_index::migration_committed(committed));
    CHECK(file_index::read_current(committed) == expected_entries());
    check_no_shadow_files(committed);
    CHECK_THROWS(file_index::OldReaderError,
                 file_index::read_legacy(committed));
    file_index::migrate(committed);
    CHECK(file_index::read_current(committed) == expected_entries());
}

void test_checksums_and_offsets_are_enforced() {
    const fs::path corrupt_data = fresh_directory("corrupt-data");
    file_index::create_legacy(corrupt_data, sample_entries());
    file_index::migrate(corrupt_data);
    const fs::path data_path = corrupt_data / "index.data.000001";
    flip_byte(data_path,
              static_cast<std::streamoff>(bytes_of(data_path).size() - 1));
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_current(corrupt_data));

    const fs::path corrupt_offsets = fresh_directory("corrupt-offsets");
    file_index::create_legacy(corrupt_offsets, sample_entries());
    file_index::migrate(corrupt_offsets);
    const fs::path offsets_path = corrupt_offsets / "index.offsets.000001";
    flip_byte(offsets_path, 16 + 8 * 5);
    replace_manifest_field(
        corrupt_offsets / "CURRENT", "offsets_checksum",
        checksum_text(checksum(bytes_of(offsets_path))));
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_current(corrupt_offsets));

    const fs::path bad_manifest_checksum =
        fresh_directory("bad-manifest-checksum");
    file_index::create_legacy(bad_manifest_checksum, sample_entries());
    file_index::migrate(bad_manifest_checksum);
    replace_manifest_field(bad_manifest_checksum / "CURRENT", "data_checksum",
                           "0000000000000000");
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_current(bad_manifest_checksum));

    const fs::path inconsistent_count = fresh_directory("inconsistent-count");
    file_index::create_legacy(inconsistent_count, sample_entries());
    file_index::migrate(inconsistent_count);
    replace_manifest_field(inconsistent_count / "CURRENT", "count", "5");
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_current(inconsistent_count));

    const fs::path corrupt_legacy = fresh_directory("corrupt-legacy");
    file_index::create_legacy(corrupt_legacy, sample_entries());
    const fs::path legacy_path = corrupt_legacy / "index.v1";
    flip_byte(legacy_path,
              static_cast<std::streamoff>(bytes_of(legacy_path).size() - 9));
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_legacy(corrupt_legacy));
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::migrate(corrupt_legacy));
}

void test_truncation_and_stale_reader_guard() {
    const fs::path truncated_legacy = fresh_directory("truncated-legacy");
    file_index::create_legacy(truncated_legacy, sample_entries());
    const fs::path legacy_path = truncated_legacy / "index.v1";
    fs::resize_file(legacy_path, fs::file_size(legacy_path) - 1);
    CHECK_THROWS(file_index::CorruptIndex,
                 file_index::read_legacy(truncated_legacy));

    for (const std::string& filename :
         {std::string("CURRENT"), std::string("index.data.000001"),
          std::string("index.offsets.000001")}) {
        const fs::path directory = fresh_directory("truncated-" + filename);
        file_index::create_legacy(directory, sample_entries());
        file_index::migrate(directory);
        const fs::path path = directory / filename;
        fs::resize_file(path, fs::file_size(path) - 1);
        CHECK_THROWS(file_index::CorruptIndex,
                     file_index::read_current(directory));
    }

    const fs::path stale_guard = fresh_directory("stale-reader-guard");
    const std::vector<Entry> original = sample_entries();
    file_index::create_legacy(stale_guard, original);
    write_text(stale_guard / "CURRENT", "incomplete v2 manifest");
    CHECK_THROWS(file_index::OldReaderError,
                 file_index::read_legacy(stale_guard));
}

}  // namespace

int main() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"complete migration contract", test_complete_migration_contract},
        {"stable duplicate ordering", test_stable_duplicate_ordering},
        {"durable commit syscall order", test_commit_syscall_order},
        {"crash recovery commit ordering",
         test_every_crash_has_one_readable_layout_and_retries},
        {"checksums and offsets", test_checksums_and_offsets_are_enforced},
        {"truncation and stale-reader guard",
         test_truncation_and_stale_reader_guard},
    };

    int failures = 0;
    for (const auto& test : tests) {
        try {
            test.second();
            std::cout << "PASS: " << test.first << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL: " << test.first << ": " << error.what() << '\n';
        }
    }
    fs::remove_all("test-tmp");
    if (failures != 0) {
        std::cerr << failures << " protected test(s) failed\n";
        return 1;
    }
    std::cout << "All protected tests passed\n";
    return 0;
}
