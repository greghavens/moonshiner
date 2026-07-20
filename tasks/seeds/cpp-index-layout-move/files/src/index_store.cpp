#include "index_store.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <charconv>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <sstream>
#include <string_view>
#include <system_error>
#include <unistd.h>

namespace file_index {
namespace {

namespace fs = std::filesystem;

constexpr std::array<unsigned char, 8> kLegacyMagic = {
    'M', 'I', 'D', 'X', 'V', '1', 0, 0};
constexpr std::array<unsigned char, 8> kDataMagic = {
    'M', 'I', 'D', 'X', 'D', '2', 0, 0};
constexpr std::array<unsigned char, 8> kOffsetsMagic = {
    'M', 'I', 'D', 'X', 'O', '2', 0, 0};

constexpr std::string_view kGeneration = "000001";
constexpr std::string_view kDataName = "index.data.000001";
constexpr std::string_view kOffsetsName = "index.offsets.000001";
constexpr std::string_view kCurrentName = "CURRENT";
constexpr std::string_view kDataShadow = "index.data.000001.shadow";
constexpr std::string_view kOffsetsShadow = "index.offsets.000001.shadow";
constexpr std::string_view kManifestShadow = "CURRENT.shadow";

using Bytes = std::vector<unsigned char>;

[[noreturn]] void system_failure(const std::string& action, const fs::path& path) {
    throw std::runtime_error(action + " " + path.string() + ": " +
                             std::strerror(errno));
}

void append_u32(Bytes& out, std::uint32_t value) {
    for (unsigned shift = 0; shift != 32; shift += 8) {
        out.push_back(static_cast<unsigned char>((value >> shift) & 0xffU));
    }
}

void append_u64(Bytes& out, std::uint64_t value) {
    for (unsigned shift = 0; shift != 64; shift += 8) {
        out.push_back(static_cast<unsigned char>((value >> shift) & 0xffU));
    }
}

void append_string(Bytes& out, const std::string& value) {
    out.insert(out.end(), value.begin(), value.end());
}

std::uint64_t checksum(const Bytes& bytes) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (const unsigned char byte : bytes) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string checksum_text(std::uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << value;
    return out.str();
}

class Cursor {
public:
    Cursor(const Bytes& bytes, std::string label)
        : bytes_(bytes), label_(std::move(label)) {}

    void expect_magic(const std::array<unsigned char, 8>& magic) {
        require(magic.size());
        if (!std::equal(magic.begin(), magic.end(), bytes_.begin() + position_)) {
            throw CorruptIndex(label_ + " has the wrong magic");
        }
        position_ += magic.size();
    }

    std::uint32_t u32() {
        require(4);
        std::uint32_t value = 0;
        for (unsigned shift = 0; shift != 32; shift += 8) {
            value |= static_cast<std::uint32_t>(bytes_[position_++]) << shift;
        }
        return value;
    }

    std::uint64_t u64() {
        require(8);
        std::uint64_t value = 0;
        for (unsigned shift = 0; shift != 64; shift += 8) {
            value |= static_cast<std::uint64_t>(bytes_[position_++]) << shift;
        }
        return value;
    }

    std::string string(std::size_t length) {
        require(length);
        const auto first = bytes_.begin() + static_cast<std::ptrdiff_t>(position_);
        position_ += length;
        return std::string(first, first + static_cast<std::ptrdiff_t>(length));
    }

    std::size_t position() const { return position_; }
    std::size_t remaining() const { return bytes_.size() - position_; }

private:
    void require(std::size_t amount) const {
        if (amount > bytes_.size() - position_) {
            throw CorruptIndex(label_ + " is truncated");
        }
    }

    const Bytes& bytes_;
    std::string label_;
    std::size_t position_ = 0;
};

Bytes read_bytes(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw CorruptIndex("cannot open " + path.filename().string());
    }
    return Bytes(std::istreambuf_iterator<char>(input),
                 std::istreambuf_iterator<char>());
}

void write_all(int descriptor, const Bytes& bytes, const fs::path& path) {
    std::size_t written = 0;
    while (written < bytes.size()) {
        const ssize_t amount = ::write(descriptor, bytes.data() + written,
                                       bytes.size() - written);
        if (amount < 0 && errno == EINTR) {
            continue;
        }
        if (amount <= 0) {
            system_failure("cannot write", path);
        }
        written += static_cast<std::size_t>(amount);
    }
}

void write_durable(const fs::path& path, const Bytes& bytes) {
    const int descriptor = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (descriptor < 0) {
        system_failure("cannot create", path);
    }
    try {
        write_all(descriptor, bytes, path);
        if (::fsync(descriptor) != 0) {
            system_failure("cannot sync", path);
        }
    } catch (...) {
        const int saved_errno = errno;
        ::close(descriptor);
        errno = saved_errno;
        throw;
    }
    if (::close(descriptor) != 0) {
        system_failure("cannot close", path);
    }
}

void sync_directory(const fs::path& directory) {
    const int descriptor = ::open(directory.c_str(), O_RDONLY | O_DIRECTORY);
    if (descriptor < 0) {
        system_failure("cannot open directory", directory);
    }
    if (::fsync(descriptor) != 0) {
        const int saved_errno = errno;
        ::close(descriptor);
        errno = saved_errno;
        system_failure("cannot sync directory", directory);
    }
    if (::close(descriptor) != 0) {
        system_failure("cannot close directory", directory);
    }
}

void atomic_rename(const fs::path& from, const fs::path& to) {
    if (::rename(from.c_str(), to.c_str()) != 0) {
        system_failure("cannot rename " + from.string() + " to", to);
    }
}

void remove_if_present(const fs::path& path) {
    std::error_code error;
    const bool removed = fs::remove(path, error);
    (void)removed;
    if (error) {
        throw std::runtime_error("cannot remove " + path.string() + ": " +
                                 error.message());
    }
}

void crash_if(CrashPoint selected, CrashPoint reached) {
    if (selected == reached) {
        throw SimulatedCrash("simulated process interruption");
    }
}

void append_record(Bytes& bytes, const Entry& entry) {
    if (entry.key.size() > std::numeric_limits<std::uint32_t>::max() ||
        entry.value.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw std::length_error("index entry is too large");
    }
    append_u32(bytes, static_cast<std::uint32_t>(entry.key.size()));
    append_u32(bytes, static_cast<std::uint32_t>(entry.value.size()));
    append_string(bytes, entry.key);
    append_string(bytes, entry.value);
}

Entry read_record(Cursor& cursor) {
    const std::uint32_t key_length = cursor.u32();
    const std::uint32_t value_length = cursor.u32();
    Entry result;
    result.key = cursor.string(key_length);
    result.value = cursor.string(value_length);
    return result;
}

Bytes encode_legacy(const std::vector<Entry>& entries) {
    Bytes bytes(kLegacyMagic.begin(), kLegacyMagic.end());
    append_u64(bytes, entries.size());
    for (const Entry& entry : entries) {
        append_record(bytes, entry);
    }
    append_u64(bytes, checksum(bytes));
    return bytes;
}

std::vector<Entry> decode_legacy(const Bytes& bytes) {
    if (bytes.size() < kLegacyMagic.size() + 16) {
        throw CorruptIndex("index.v1 is truncated");
    }
    Bytes body(bytes.begin(), bytes.end() - 8);
    std::uint64_t stored_checksum = 0;
    for (unsigned shift = 0; shift != 64; shift += 8) {
        const std::size_t index = bytes.size() - 8 + shift / 8;
        stored_checksum |= static_cast<std::uint64_t>(bytes[index]) << shift;
    }
    if (stored_checksum != checksum(body)) {
        throw CorruptIndex("index.v1 checksum mismatch");
    }

    Cursor cursor(body, "index.v1");
    cursor.expect_magic(kLegacyMagic);
    const std::uint64_t count = cursor.u64();
    if (count > body.size()) {
        throw CorruptIndex("index.v1 has an impossible record count");
    }
    std::vector<Entry> entries;
    entries.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t index = 0; index < count; ++index) {
        entries.push_back(read_record(cursor));
    }
    if (cursor.remaining() != 0) {
        throw CorruptIndex("index.v1 has trailing bytes");
    }
    return entries;
}

struct NewLayout {
    Bytes data;
    Bytes offsets;
    std::vector<Entry> ordered_entries;
};

bool key_less(const Entry& left, const Entry& right) {
    return std::lexicographical_compare(
        left.key.begin(), left.key.end(), right.key.begin(), right.key.end(),
        [](char lhs, char rhs) {
            return static_cast<unsigned char>(lhs) < static_cast<unsigned char>(rhs);
        });
}

NewLayout build_layout(std::vector<Entry> entries) {
    std::stable_sort(entries.begin(), entries.end(), key_less);

    NewLayout layout;
    layout.ordered_entries = std::move(entries);
    layout.data.assign(kDataMagic.begin(), kDataMagic.end());
    append_u64(layout.data, layout.ordered_entries.size());
    layout.offsets.assign(kOffsetsMagic.begin(), kOffsetsMagic.end());
    append_u64(layout.offsets, layout.ordered_entries.size());
    for (const Entry& entry : layout.ordered_entries) {
        append_u64(layout.offsets, layout.data.size());
        append_record(layout.data, entry);
    }
    return layout;
}

Bytes make_manifest(const NewLayout& layout) {
    std::ostringstream text;
    text << "INDEX-LAYOUT 2\n"
         << "generation " << kGeneration << '\n'
         << "count " << layout.ordered_entries.size() << '\n'
         << "data " << kDataName << '\n'
         << "offsets " << kOffsetsName << '\n'
         << "data_checksum " << checksum_text(checksum(layout.data)) << '\n'
         << "offsets_checksum " << checksum_text(checksum(layout.offsets)) << '\n';
    const std::string value = text.str();
    return Bytes(value.begin(), value.end());
}

std::uint64_t parse_number(const std::string& value, int base,
                           const std::string& field) {
    std::uint64_t parsed = 0;
    const char* const begin = value.data();
    const char* const end = begin + value.size();
    const auto result = std::from_chars(begin, end, parsed, base);
    if (result.ec != std::errc() || result.ptr != end) {
        throw CorruptIndex("CURRENT has invalid " + field);
    }
    return parsed;
}

struct Manifest {
    std::uint64_t count;
    std::uint64_t data_checksum;
    std::uint64_t offsets_checksum;
};

Manifest parse_manifest(const Bytes& bytes) {
    if (bytes.empty() || bytes.back() != '\n') {
        throw CorruptIndex("CURRENT is truncated");
    }
    const std::string text(bytes.begin(), bytes.end());
    std::istringstream input(text);
    std::string heading;
    if (!std::getline(input, heading) || heading != "INDEX-LAYOUT 2") {
        throw CorruptIndex("CURRENT has the wrong layout version");
    }

    std::map<std::string, std::string> fields;
    std::string line;
    while (std::getline(input, line)) {
        std::istringstream row(line);
        std::string key;
        std::string value;
        std::string extra;
        if (!(row >> key >> value) || (row >> extra) || !fields.emplace(key, value).second) {
            throw CorruptIndex("CURRENT has an invalid field");
        }
    }
    const std::array<std::string, 6> required = {
        "generation", "count", "data", "offsets", "data_checksum",
        "offsets_checksum"};
    if (fields.size() != required.size()) {
        throw CorruptIndex("CURRENT has missing or extra fields");
    }
    for (const std::string& field : required) {
        if (fields.find(field) == fields.end()) {
            throw CorruptIndex("CURRENT is missing " + field);
        }
    }
    if (fields.at("generation") != kGeneration || fields.at("data") != kDataName ||
        fields.at("offsets") != kOffsetsName) {
        throw CorruptIndex("CURRENT refers to an unexpected generation");
    }
    return Manifest{parse_number(fields.at("count"), 10, "count"),
                    parse_number(fields.at("data_checksum"), 16, "data checksum"),
                    parse_number(fields.at("offsets_checksum"), 16,
                                 "offsets checksum")};
}

std::vector<Entry> decode_current(const Bytes& data, const Bytes& offsets,
                                  const Manifest& manifest) {
    if (checksum(data) != manifest.data_checksum) {
        throw CorruptIndex("v2 data checksum mismatch");
    }
    if (checksum(offsets) != manifest.offsets_checksum) {
        throw CorruptIndex("v2 offsets checksum mismatch");
    }

    Cursor offset_cursor(offsets, "v2 offsets");
    offset_cursor.expect_magic(kOffsetsMagic);
    const std::uint64_t offset_count = offset_cursor.u64();
    Cursor data_cursor(data, "v2 data");
    data_cursor.expect_magic(kDataMagic);
    const std::uint64_t data_count = data_cursor.u64();
    if (manifest.count != data_count || manifest.count != offset_count ||
        manifest.count > data.size()) {
        throw CorruptIndex("v2 record counts disagree");
    }

    std::vector<Entry> entries;
    entries.reserve(static_cast<std::size_t>(manifest.count));
    for (std::uint64_t index = 0; index < manifest.count; ++index) {
        const std::uint64_t stored_offset = offset_cursor.u64();
        if (stored_offset != data_cursor.position()) {
            throw CorruptIndex("v2 offset does not match its record");
        }
        entries.push_back(read_record(data_cursor));
    }
    if (data_cursor.remaining() != 0 || offset_cursor.remaining() != 0) {
        throw CorruptIndex("v2 generation has trailing bytes");
    }
    if (!std::is_sorted(entries.begin(), entries.end(), key_less)) {
        throw CorruptIndex("v2 records are not key ordered");
    }
    return entries;
}

}  // namespace

void create_legacy(const fs::path& directory, const std::vector<Entry>& entries) {
    fs::create_directories(directory);
    write_durable(directory / "index.v1", encode_legacy(entries));
    sync_directory(directory);
}

bool migration_committed(const fs::path& directory) {
    std::error_code error;
    const bool result = fs::exists(directory / kCurrentName, error);
    if (error) {
        throw std::runtime_error("cannot inspect CURRENT: " + error.message());
    }
    return result;
}

std::vector<Entry> read_legacy(const fs::path& directory) {
    if (migration_committed(directory)) {
        throw OldReaderError("legacy reader detected committed v2 layout");
    }
    return decode_legacy(read_bytes(directory / "index.v1"));
}

std::vector<Entry> read_current(const fs::path& directory) {
    if (!migration_committed(directory)) {
        throw NoCurrentIndex("CURRENT does not exist");
    }
    const Manifest manifest = parse_manifest(read_bytes(directory / kCurrentName));
    return decode_current(read_bytes(directory / kDataName),
                          read_bytes(directory / kOffsetsName), manifest);
}

void migrate(const fs::path& directory, CrashPoint crash_point) {
    if (migration_committed(directory)) {
        (void)read_current(directory);
        return;
    }

    const std::vector<Entry> legacy = read_legacy(directory);
    const NewLayout layout = build_layout(legacy);

    remove_if_present(directory / kDataShadow);
    remove_if_present(directory / kOffsetsShadow);
    remove_if_present(directory / kManifestShadow);

    write_durable(directory / kDataShadow, layout.data);
    crash_if(crash_point, CrashPoint::AfterDataShadow);
    write_durable(directory / kOffsetsShadow, layout.offsets);
    crash_if(crash_point, CrashPoint::AfterOffsetsShadow);
    write_durable(directory / kManifestShadow, make_manifest(layout));
    crash_if(crash_point, CrashPoint::AfterManifestShadow);

    // CURRENT is the reader-visible commit point. It must never name a partial
    // generation, including when the process stops between these renames.
    atomic_rename(directory / kManifestShadow, directory / kCurrentName);
    crash_if(crash_point, CrashPoint::AfterManifestRename);
    atomic_rename(directory / kDataShadow, directory / kDataName);
    crash_if(crash_point, CrashPoint::AfterDataRename);
    atomic_rename(directory / kOffsetsShadow, directory / kOffsetsName);
    crash_if(crash_point, CrashPoint::AfterOffsetsRename);
    sync_directory(directory);
}

}  // namespace file_index
