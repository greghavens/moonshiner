#pragma once

#include "snapshot_store.hpp"

#include <filesystem>
#include <functional>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace snapshotreplace::test {

inline void require(bool condition, std::string_view message) {
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

template <typename Left, typename Right>
void require_equal(const Left& left, const Right& right,
                   std::string_view message) {
    if (!(left == right)) {
        throw std::runtime_error(std::string(message));
    }
}

class TestDirectory {
public:
    explicit TestDirectory(std::string name)
        : path_(std::filesystem::current_path() / ".test-tmp" /
                std::move(name)) {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
        if (error) {
            throw std::runtime_error("could not reset test directory");
        }
        std::filesystem::create_directories(path_);
    }

    ~TestDirectory() {
        std::error_code ignored;
        std::filesystem::remove_all(path_, ignored);
    }

    std::filesystem::path file(std::string_view name) const {
        return path_ / std::string(name);
    }

private:
    std::filesystem::path path_;
};

struct Failure {
    FileOperation operation;
    int native_code;
    int remaining;
};

class InjectingFileSystem;

class InjectingWritableFile final : public WritableFile {
public:
    InjectingWritableFile(InjectingFileSystem& owner,
                          std::unique_ptr<WritableFile> delegate)
        : owner_(owner), delegate_(std::move(delegate)) {}

    void write(std::string_view bytes) override;
    void flush() override;

private:
    InjectingFileSystem& owner_;
    std::unique_ptr<WritableFile> delegate_;
};

class InjectingFileSystem final : public FileSystemAdapter {
public:
    void fail_next(FileOperation operation, int native_code) {
        failure_ = Failure{operation, native_code, 1};
    }

    void clear_failure() { failure_.reset(); }

    void inject(FileOperation operation) {
        if (failure_ && failure_->operation == operation &&
            failure_->remaining > 0) {
            --failure_->remaining;
            const int code = failure_->native_code;
            throw PlatformError(operation, code,
                                std::string("injected ") +
                                    operation_name(operation) + " failure");
        }
    }

    std::unique_ptr<WritableFile> open_for_write(
        const std::filesystem::path& path) override {
        inject(FileOperation::Open);
        return std::make_unique<InjectingWritableFile>(
            *this, native_.open_for_write(path));
    }

    void atomic_replace(const std::filesystem::path& staged,
                        const std::filesystem::path& live) override {
        inject(FileOperation::Rename);
        native_.atomic_replace(staged, live);
    }

    std::string read_all(const std::filesystem::path& path) override {
        inject(FileOperation::Read);
        return native_.read_all(path);
    }

    void remove_if_exists(const std::filesystem::path& path) override {
        inject(FileOperation::Remove);
        native_.remove_if_exists(path);
    }

    bool exists(const std::filesystem::path& path) const override {
        return native_.exists(path);
    }

private:
    NativeFileSystem native_;
    std::optional<Failure> failure_;
};

inline void InjectingWritableFile::write(std::string_view bytes) {
    owner_.inject(FileOperation::Write);
    delegate_->write(bytes);
}

inline void InjectingWritableFile::flush() {
    owner_.inject(FileOperation::Flush);
    delegate_->flush();
}

using TestFunction = void (*)();

struct TestCase {
    const char* name;
    TestFunction function;
};

inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> tests;
    return tests;
}

class Registration {
public:
    Registration(const char* name, TestFunction function) {
        registry().push_back(TestCase{name, function});
    }
};

}  // namespace snapshotreplace::test

#define SNAPSHOT_TEST(name)                                      \
    static void name();                                          \
    static ::snapshotreplace::test::Registration registration_##name( \
        #name, &name);                                           \
    static void name()
