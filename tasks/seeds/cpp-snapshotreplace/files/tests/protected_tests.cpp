#include "test_support.hpp"

#include <array>

using namespace snapshotreplace;
using namespace snapshotreplace::test;

namespace {

void require_failed_replace_preserves_prior(FileOperation failed_operation,
                                            int native_code,
                                            const std::string& case_name) {
    TestDirectory directory(case_name);
    InjectingFileSystem files;
    SnapshotStore store(files, directory.file("state.snapshot"));
    store.replace("last known good");

    files.fail_next(failed_operation, native_code);
    bool saw_expected_error = false;
    try {
        store.replace("uncommitted replacement");
    } catch (const PlatformError& error) {
        saw_expected_error = error.operation() == failed_operation &&
                             error.native_code() == native_code;
    }
    require(saw_expected_error,
            "replace did not preserve the adapter operation and native code");

    files.clear_failure();
    require_equal(store.read(), std::string("last known good"),
                  "failed publication did not retain the prior snapshot");
    require(!files.exists(store.staging_path()),
            "failed publication left staging data behind");
}

}  // namespace

SNAPSHOT_TEST(open_failure_retains_prior_snapshot) {
    require_failed_replace_preserves_prior(FileOperation::Open, 7100,
                                           "open-failure");
}

SNAPSHOT_TEST(write_failure_retains_prior_snapshot) {
    require_failed_replace_preserves_prior(FileOperation::Write, 7101,
                                           "write-failure");
}

SNAPSHOT_TEST(flush_failure_retains_prior_snapshot) {
    require_failed_replace_preserves_prior(FileOperation::Flush, 7102,
                                           "flush-failure");
}

SNAPSHOT_TEST(rename_failure_retains_prior_snapshot) {
    require_failed_replace_preserves_prior(FileOperation::Rename, 7103,
                                           "rename-failure");
}

SNAPSHOT_TEST(rename_failure_allows_idempotent_recovery_and_retry) {
    TestDirectory directory("rename-retry");
    InjectingFileSystem files;
    SnapshotStore store(files, directory.file("state.snapshot"));
    store.replace("generation one");

    files.fail_next(FileOperation::Rename, 7201);
    try {
        store.replace("generation two");
        throw std::runtime_error("injected rename failure was not reported");
    } catch (const PlatformError& error) {
        require(error.operation() == FileOperation::Rename,
                "wrong publication error operation");
        require(error.native_code() == 7201,
                "wrong publication native error code");
    }

    files.clear_failure();
    store.recover();
    store.recover();
    require_equal(store.read(), std::string("generation one"),
                  "recovery lost the previous committed generation");

    store.replace("generation two");
    require_equal(store.read(), std::string("generation two"),
                  "retry did not publish the replacement generation");
}
