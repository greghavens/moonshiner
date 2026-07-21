package com.moonshiner.migration;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class JsonColumnMigratorTest {
    public static void main(String[] args) {
        migratesEachVersionWithoutDroppingUnknownFieldsAndQuarantinesMalformedRows();
        keepsCommittedCheckpointsWhenALaterWriteFails();
        System.out.println("All JsonColumnMigrator tests passed");
    }

    private static void migratesEachVersionWithoutDroppingUnknownFieldsAndQuarantinesMalformedRows() {
        TransactionalStore store = new TransactionalStore();
        store.add(1, "{\"_schemaVersion\":1,\"display_name\":\"Ada\",\"contact_email\":\"ada@example.test\",\"unknown\":{\"source\":\"legacy\"},\"tags\":[\"math\",2]}");
        store.add(2, "{\"_schemaVersion\":2,\"displayName\":\"Grace\",\"contact_email\":\"grace@example.test\",\"futureFlag\":true}");
        store.add(3, "{\"_schemaVersion\":3,\"displayName\":\"Linus\",\"opaque\":null}");
        String malformed = "{\"_schemaVersion\":1,\"display_name\":\"broken\"";
        store.add(4, malformed);

        JsonColumnMigrator.MigrationReport report = new JsonColumnMigrator(store).migrate(2);

        assertEquals(4, report.scanned(), "scanned rows");
        assertEquals(2, report.migrated(), "migrated rows");
        assertEquals(1, report.quarantined(), "quarantined rows");
        assertEquals(4L, store.checkpoint(), "final checkpoint");

        JsonObjectCodec codec = new JsonObjectCodec();
        Map<String, Object> first = codec.parseObject(store.json(1));
        assertEquals(new BigDecimal("3"), first.get("_schemaVersion"), "v1 reaches current version");
        assertEquals("Ada", first.get("displayName"), "v1 field rename");
        assertEquals("ada@example.test", first.get("email"), "all migration steps applied");
        assertTrue(!first.containsKey("display_name") && !first.containsKey("contact_email"), "legacy keys removed");
        assertEquals("legacy", nested(first, "unknown", "source"), "unknown object retained");
        assertEquals(new BigDecimal("2"), ((List<?>) first.get("tags")).get(1), "unknown array retained");

        Map<String, Object> second = codec.parseObject(store.json(2));
        assertEquals(new BigDecimal("3"), second.get("_schemaVersion"), "v2 reaches current version");
        assertEquals(Boolean.TRUE, second.get("futureFlag"), "unknown scalar retained");
        assertEquals("grace@example.test", second.get("email"), "v2 migration applied");
        assertEquals(malformed, store.json(4), "malformed JSON left untouched");
        assertTrue(store.quarantineReason(4) != null, "malformed row isolated");
    }

    private static void keepsCommittedCheckpointsWhenALaterWriteFails() {
        TransactionalStore store = new TransactionalStore();
        store.add(10, v1("ten"));
        store.add(11, v1("eleven"));
        store.add(12, v1("twelve"));
        store.add(13, v1("thirteen"));
        store.failWritesFor(13);

        assertThrows(StorageFailure.class, () -> new JsonColumnMigrator(store).migrate(2),
                "later storage failure must escape");

        assertEquals(11L, store.checkpoint(), "last completed checkpoint remains durable");
        assertVersion(3, store.json(10), "first checkpoint row 10 committed");
        assertVersion(3, store.json(11), "first checkpoint row 11 committed");
        assertVersion(1, store.json(12), "work after checkpoint rolled back");
        assertVersion(1, store.json(13), "failed row unchanged");
        assertTrue(!store.hasActiveTransaction(), "failed chunk transaction closed");
    }

    private static String v1(String name) {
        return "{\"_schemaVersion\":1,\"display_name\":\"" + name + "\",\"custom\":\"keep\"}";
    }

    private static void assertVersion(int expected, String json, String message) {
        Object actual = new JsonObjectCodec().parseObject(json).get("_schemaVersion");
        assertEquals(BigDecimal.valueOf(expected), actual, message);
    }

    private static Object nested(Map<String, Object> document, String objectName, String fieldName) {
        @SuppressWarnings("unchecked")
        Map<String, Object> nested = (Map<String, Object>) document.get(objectName);
        return nested.get(fieldName);
    }

    private static void assertThrows(Class<? extends Throwable> expected, Runnable action, String message) {
        try {
            action.run();
        } catch (Throwable actual) {
            if (expected.isInstance(actual)) return;
            throw new AssertionError(message + ": expected " + expected.getName() + " but got " + actual, actual);
        }
        throw new AssertionError(message + ": expected " + expected.getName());
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static final class StorageFailure extends RuntimeException {
        private StorageFailure(String message) {
            super(message);
        }
    }

    private static final class TransactionalStore implements DocumentStore {
        private final LinkedHashMap<Long, String> committedRows = new LinkedHashMap<>();
        private final LinkedHashMap<Long, String> committedQuarantine = new LinkedHashMap<>();
        private long committedCheckpoint;
        private long failingRow = Long.MIN_VALUE;
        private Tx active;

        private void add(long id, String json) {
            committedRows.put(id, json);
        }

        private void failWritesFor(long id) {
            failingRow = id;
        }

        private String json(long id) {
            return committedRows.get(id);
        }

        private String quarantineReason(long id) {
            return committedQuarantine.get(id);
        }

        private long checkpoint() {
            return committedCheckpoint;
        }

        private boolean hasActiveTransaction() {
            return active != null;
        }

        @Override
        public long loadCheckpoint() {
            return committedCheckpoint;
        }

        @Override
        public List<Row> rowsAfter(long checkpoint) {
            List<Row> result = new ArrayList<>();
            for (Map.Entry<Long, String> entry : committedRows.entrySet()) {
                if (entry.getKey() > checkpoint) result.add(new Row(entry.getKey(), entry.getValue()));
            }
            Collections.sort(result, (left, right) -> Long.compare(left.id(), right.id()));
            return result;
        }

        @Override
        public Transaction beginTransaction() {
            if (active != null) throw new IllegalStateException("transaction already active");
            active = new Tx();
            return active;
        }

        private final class Tx implements Transaction {
            private final LinkedHashMap<Long, String> replacements = new LinkedHashMap<>();
            private final LinkedHashMap<Long, String> quarantines = new LinkedHashMap<>();
            private Long checkpoint;
            private boolean open = true;

            @Override
            public void replaceJson(long rowId, String json) {
                requireOpen();
                if (rowId == failingRow) throw new StorageFailure("simulated write failure for row " + rowId);
                replacements.put(rowId, json);
            }

            @Override
            public void quarantine(long rowId, String reason) {
                requireOpen();
                quarantines.put(rowId, reason);
            }

            @Override
            public void saveCheckpoint(long rowId) {
                requireOpen();
                checkpoint = rowId;
            }

            @Override
            public void commit() {
                requireOpen();
                committedRows.putAll(replacements);
                committedQuarantine.putAll(quarantines);
                if (checkpoint != null) committedCheckpoint = checkpoint;
                close();
            }

            @Override
            public void rollback() {
                requireOpen();
                close();
            }

            private void requireOpen() {
                if (!open) throw new IllegalStateException("transaction is closed");
            }

            private void close() {
                open = false;
                active = null;
            }
        }
    }
}
