#include "snapshot_store.hpp"

#include <array>
#include <cerrno>
#include <charconv>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <system_error>
#include <utility>

#ifdef _WIN32
#include <Windows.h>
#include <io.h>
#else
#include <unistd.h>
#endif

namespace snapshotreplace {
namespace {

constexpr std::string_view kMagic = "SNAPSHOT/1\n";

std::string error_message(FileOperation operation, int code,
                          std::string_view detail) {
    std::ostringstream out;
    out << operation_name(operation) << " failed (" << code << "): " << detail;
    return out.str();
}

[[noreturn]] void throw_errno(FileOperation operation,
                              std::string_view detail) {
    const int code = errno;
    throw PlatformError(operation, code,
                        error_message(operation, code, detail));
}

std::uint64_t checksum(std::string_view bytes) {
    std::uint64_t value = 14695981039346656037ULL;
    for (const unsigned char byte : bytes) {
        value ^= byte;
        value *= 1099511628211ULL;
    }
    return value;
}

std::string encode(std::string_view payload) {
    std::ostringstream out;
    out << kMagic << std::hex << std::setfill('0') << std::setw(16)
        << checksum(payload) << '\n'
        << std::dec << payload.size() << '\n';
    out.write(payload.data(), static_cast<std::streamsize>(payload.size()));
    return out.str();
}

std::string parse(std::string_view document) {
    if (document.substr(0, kMagic.size()) != kMagic) {
        throw SnapshotError("invalid snapshot header");
    }

    const std::size_t checksum_start = kMagic.size();
    const std::size_t checksum_end = document.find('\n', checksum_start);
    if (checksum_end == std::string_view::npos) {
        throw SnapshotError("missing snapshot checksum");
    }
    const std::string_view checksum_text =
        document.substr(checksum_start, checksum_end - checksum_start);
    if (checksum_text.size() != 16) {
        throw SnapshotError("invalid snapshot checksum");
    }
    std::uint64_t expected_checksum = 0;
    const auto checksum_result = std::from_chars(
        checksum_text.data(), checksum_text.data() + checksum_text.size(),
        expected_checksum, 16);
    if (checksum_result.ec != std::errc{} ||
        checksum_result.ptr != checksum_text.data() + checksum_text.size()) {
        throw SnapshotError("invalid snapshot checksum");
    }

    const std::size_t length_start = checksum_end + 1;
    const std::size_t length_end = document.find('\n', length_start);
    if (length_end == std::string_view::npos) {
        throw SnapshotError("missing snapshot length");
    }
    const std::string_view length_text =
        document.substr(length_start, length_end - length_start);
    std::size_t expected_length = 0;
    const auto length_result = std::from_chars(
        length_text.data(), length_text.data() + length_text.size(),
        expected_length);
    if (length_text.empty() || length_result.ec != std::errc{} ||
        length_result.ptr != length_text.data() + length_text.size()) {
        throw SnapshotError("invalid snapshot length");
    }

    const std::string_view payload = document.substr(length_end + 1);
    if (payload.size() != expected_length) {
        throw SnapshotError("snapshot length mismatch");
    }
    if (checksum(payload) != expected_checksum) {
        throw SnapshotError("snapshot checksum mismatch");
    }
    return std::string(payload);
}

class NativeWritableFile final : public WritableFile {
public:
    explicit NativeWritableFile(const std::filesystem::path& path)
        : handle_(std::fopen(path.string().c_str(), "wb")) {
        if (handle_ == nullptr) {
            throw_errno(FileOperation::Open, path.string());
        }
    }

    ~NativeWritableFile() override {
        if (handle_ != nullptr) {
            std::fclose(handle_);
        }
    }

    void write(std::string_view bytes) override {
        std::size_t offset = 0;
        while (offset < bytes.size()) {
            const std::size_t written =
                std::fwrite(bytes.data() + offset, 1, bytes.size() - offset,
                            handle_);
            if (written == 0) {
                throw_errno(FileOperation::Write, "staging snapshot");
            }
            offset += written;
        }
    }

    void flush() override {
        if (std::fflush(handle_) != 0) {
            throw_errno(FileOperation::Flush, "fflush");
        }
#ifdef _WIN32
        if (_commit(_fileno(handle_)) != 0) {
            throw_errno(FileOperation::Flush, "_commit");
        }
#else
        if (::fsync(::fileno(handle_)) != 0) {
            throw_errno(FileOperation::Flush, "fsync");
        }
#endif
    }

private:
    std::FILE* handle_;
};

}  // namespace

const char* operation_name(FileOperation operation) noexcept {
    switch (operation) {
        case FileOperation::Open:
            return "open";
        case FileOperation::Write:
            return "write";
        case FileOperation::Flush:
            return "flush";
        case FileOperation::Rename:
            return "rename";
        case FileOperation::Read:
            return "read";
        case FileOperation::Remove:
            return "remove";
    }
    return "unknown";
}

PlatformError::PlatformError(FileOperation operation, int native_code,
                             std::string message)
    : std::runtime_error(std::move(message)),
      operation_(operation),
      native_code_(native_code) {}

std::unique_ptr<WritableFile> NativeFileSystem::open_for_write(
    const std::filesystem::path& path) {
    return std::make_unique<NativeWritableFile>(path);
}

void NativeFileSystem::atomic_replace(const std::filesystem::path& staged,
                                      const std::filesystem::path& live) {
#ifdef _WIN32
    if (!::MoveFileExW(staged.c_str(), live.c_str(),
                       MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        const int code = static_cast<int>(::GetLastError());
        throw PlatformError(FileOperation::Rename, code,
                            error_message(FileOperation::Rename, code,
                                          "MoveFileExW"));
    }
#else
    if (::rename(staged.c_str(), live.c_str()) != 0) {
        throw_errno(FileOperation::Rename, "rename");
    }
#endif
}

std::string NativeFileSystem::read_all(const std::filesystem::path& path) {
    errno = 0;
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        const int code = errno == 0 ? EIO : errno;
        throw PlatformError(FileOperation::Read, code,
                            error_message(FileOperation::Read, code,
                                          path.string()));
    }
    std::ostringstream contents;
    contents << input.rdbuf();
    if (input.bad()) {
        const int code = errno == 0 ? EIO : errno;
        throw PlatformError(FileOperation::Read, code,
                            error_message(FileOperation::Read, code,
                                          path.string()));
    }
    return contents.str();
}

void NativeFileSystem::remove_if_exists(const std::filesystem::path& path) {
    std::error_code error;
    std::filesystem::remove(path, error);
    if (error) {
        throw PlatformError(FileOperation::Remove, error.value(),
                            error_message(FileOperation::Remove, error.value(),
                                          path.string()));
    }
}

bool NativeFileSystem::exists(const std::filesystem::path& path) const {
    std::error_code error;
    const bool result = std::filesystem::exists(path, error);
    if (error) {
        throw PlatformError(FileOperation::Read, error.value(),
                            error_message(FileOperation::Read, error.value(),
                                          path.string()));
    }
    return result;
}

SnapshotStore::SnapshotStore(FileSystemAdapter& files,
                             std::filesystem::path snapshot_path)
    : files_(files), snapshot_path_(std::move(snapshot_path)) {}

std::filesystem::path SnapshotStore::staging_path() const {
    return std::filesystem::path(snapshot_path_.string() + ".next");
}

void SnapshotStore::replace(std::string_view payload) {
    const std::filesystem::path staged = staging_path();
    try {
        recover();
        const std::string document = encode(payload);
        {
            std::unique_ptr<WritableFile> output = files_.open_for_write(staged);
            output->write(document);
            output->flush();
        }

        // Clear the destination for adapters that cannot rename over a file.
        files_.remove_if_exists(snapshot_path_);
        files_.atomic_replace(staged, snapshot_path_);
    } catch (...) {
        try {
            files_.remove_if_exists(staged);
        } catch (...) {
            // Cleanup is best effort; report the publication error unchanged.
        }
        throw;
    }
}

std::string SnapshotStore::read() {
    return parse(files_.read_all(snapshot_path_));
}

void SnapshotStore::recover() {
    files_.remove_if_exists(staging_path());
}

}  // namespace snapshotreplace
