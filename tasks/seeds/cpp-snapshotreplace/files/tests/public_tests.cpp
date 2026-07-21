#include "test_support.hpp"

#include <fstream>
#include <iterator>

using namespace snapshotreplace;
using namespace snapshotreplace::test;

SNAPSHOT_TEST(round_trips_replaced_snapshots) {
    TestDirectory directory("round-trip");
    NativeFileSystem files;
    SnapshotStore store(files, directory.file("state.snapshot"));

    store.replace("first state");
    require_equal(store.read(), std::string("first state"),
                  "first snapshot did not round trip");

    const std::string second("second\0state\nwith bytes", 23);
    store.replace(second);
    require_equal(store.read(), second, "replacement snapshot did not round trip");
    require(!files.exists(store.staging_path()),
            "staging file remained after successful publication");
}

SNAPSHOT_TEST(rejects_corrupt_snapshots) {
    TestDirectory directory("checksum");
    NativeFileSystem files;
    SnapshotStore store(files, directory.file("state.snapshot"));
    store.replace("checksummed payload");

    std::fstream snapshot(store.snapshot_path(),
                           std::ios::in | std::ios::out | std::ios::binary);
    require(static_cast<bool>(snapshot), "could not open snapshot for corruption");
    snapshot.seekp(-1, std::ios::end);
    snapshot.put('X');
    snapshot.close();

    bool rejected = false;
    try {
        static_cast<void>(store.read());
    } catch (const SnapshotError&) {
        rejected = true;
    }
    require(rejected, "checksum corruption was accepted");
}

SNAPSHOT_TEST(recovery_is_idempotent) {
    TestDirectory directory("recovery");
    NativeFileSystem files;
    SnapshotStore store(files, directory.file("state.snapshot"));
    store.replace("committed");

    {
        std::ofstream stale(store.staging_path(), std::ios::binary);
        stale << "partial, unchecksummed bytes";
    }
    require(files.exists(store.staging_path()), "stale staging setup failed");

    store.recover();
    store.recover();

    require(!files.exists(store.staging_path()),
            "recovery did not remove stale staging data");
    require_equal(store.read(), std::string("committed"),
                  "recovery changed the committed snapshot");
}

int main() {
    int failures = 0;
    for (const TestCase& test : registry()) {
        try {
            test.function();
            std::cout << "PASS " << test.name << '\n';
        } catch (const std::exception& error) {
            ++failures;
            std::cerr << "FAIL " << test.name << ": " << error.what() << '\n';
        }
    }
    if (failures != 0) {
        std::cerr << failures << " test(s) failed\n";
        return 1;
    }
    return 0;
}
