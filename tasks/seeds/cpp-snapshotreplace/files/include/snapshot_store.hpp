#pragma once

#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

namespace snapshotreplace {

enum class FileOperation {
    Open,
    Write,
    Flush,
    Rename,
    Read,
    Remove,
};

const char* operation_name(FileOperation operation) noexcept;

class PlatformError : public std::runtime_error {
public:
    PlatformError(FileOperation operation, int native_code, std::string message);

    FileOperation operation() const noexcept { return operation_; }
    int native_code() const noexcept { return native_code_; }

private:
    FileOperation operation_;
    int native_code_;
};

class SnapshotError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class WritableFile {
public:
    virtual ~WritableFile() = default;
    virtual void write(std::string_view bytes) = 0;
    virtual void flush() = 0;
};

class FileSystemAdapter {
public:
    virtual ~FileSystemAdapter() = default;
    virtual std::unique_ptr<WritableFile> open_for_write(
        const std::filesystem::path& path) = 0;
    virtual void atomic_replace(const std::filesystem::path& staged,
                                const std::filesystem::path& live) = 0;
    virtual std::string read_all(const std::filesystem::path& path) = 0;
    virtual void remove_if_exists(const std::filesystem::path& path) = 0;
    virtual bool exists(const std::filesystem::path& path) const = 0;
};

class NativeFileSystem final : public FileSystemAdapter {
public:
    std::unique_ptr<WritableFile> open_for_write(
        const std::filesystem::path& path) override;
    void atomic_replace(const std::filesystem::path& staged,
                        const std::filesystem::path& live) override;
    std::string read_all(const std::filesystem::path& path) override;
    void remove_if_exists(const std::filesystem::path& path) override;
    bool exists(const std::filesystem::path& path) const override;
};

class SnapshotStore {
public:
    SnapshotStore(FileSystemAdapter& files, std::filesystem::path snapshot_path);

    void replace(std::string_view payload);
    std::string read();
    void recover();

    const std::filesystem::path& snapshot_path() const noexcept {
        return snapshot_path_;
    }
    std::filesystem::path staging_path() const;

private:
    FileSystemAdapter& files_;
    std::filesystem::path snapshot_path_;
};

}  // namespace snapshotreplace
