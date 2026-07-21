package com.moonshiner.migration;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/** Migrates JSON-column rows to the current document schema. */
public final class JsonColumnMigrator {
    static final int CURRENT_VERSION = 3;

    private final DocumentStore store;
    private final JsonObjectCodec codec;

    public JsonColumnMigrator(DocumentStore store) {
        this.store = store;
        this.codec = new JsonObjectCodec();
    }

    public MigrationReport migrate(int checkpointEvery) {
        if (checkpointEvery <= 0) {
            throw new IllegalArgumentException("checkpointEvery must be positive");
        }

        List<DocumentStore.Row> rows = store.rowsAfter(store.loadCheckpoint());
        if (rows.isEmpty()) {
            return new MigrationReport(0, 0, 0);
        }

        DocumentStore.Transaction transaction = store.beginTransaction();
        int scanned = 0;
        int migrated = 0;
        int quarantined = 0;
        int sinceCheckpoint = 0;
        long lastRowId = -1;

        try {
            for (DocumentStore.Row row : rows) {
                lastRowId = row.id();
                scanned++;
                sinceCheckpoint++;

                try {
                    Map<String, Object> document = codec.parseObject(row.json());
                    int originalVersion = schemaVersion(document);
                    migrateDocument(document, originalVersion);
                    if (originalVersion != CURRENT_VERSION) {
                        transaction.replaceJson(row.id(), codec.writeObject(document));
                        migrated++;
                    }
                } catch (InvalidDocumentException | IllegalArgumentException malformed) {
                    transaction.quarantine(row.id(), malformed.getMessage());
                    quarantined++;
                }

                if (sinceCheckpoint == checkpointEvery) {
                    transaction.saveCheckpoint(row.id());
                    // A checkpoint must define a durable transaction boundary.
                    // Currently the transaction remains open until the full scan ends.
                    sinceCheckpoint = 0;
                }
            }

            if (sinceCheckpoint > 0) {
                transaction.saveCheckpoint(lastRowId);
            }
            transaction.commit();
            return new MigrationReport(scanned, migrated, quarantined);
        } catch (RuntimeException failure) {
            transaction.rollback();
            throw failure;
        }
    }

    private int schemaVersion(Map<String, Object> document) {
        Object raw = document.get("_schemaVersion");
        if (!(raw instanceof BigDecimal)) {
            throw new InvalidDocumentException("_schemaVersion must be an integer");
        }

        try {
            int version = ((BigDecimal) raw).intValueExact();
            if (version < 1 || version > CURRENT_VERSION) {
                throw new InvalidDocumentException("unsupported _schemaVersion: " + version);
            }
            return version;
        } catch (ArithmeticException notAnInteger) {
            throw new InvalidDocumentException("_schemaVersion must be an integer");
        }
    }

    private void migrateDocument(Map<String, Object> document, int version) {
        int nextVersion = version;
        while (nextVersion < CURRENT_VERSION) {
            if (nextVersion == 1) {
                renameIfPresent(document, "display_name", "displayName");
            } else if (nextVersion == 2) {
                renameIfPresent(document, "contact_email", "email");
            }
            nextVersion++;
            document.put("_schemaVersion", BigDecimal.valueOf(nextVersion));
        }
    }

    private void renameIfPresent(Map<String, Object> document, String oldName, String newName) {
        if (!document.containsKey(oldName)) {
            return;
        }
        Object value = document.remove(oldName);
        if (!document.containsKey(newName)) {
            document.put(newName, value);
        }
    }

    private static final class InvalidDocumentException extends RuntimeException {
        private InvalidDocumentException(String message) {
            super(message);
        }
    }

    public static final class MigrationReport {
        private final int scanned;
        private final int migrated;
        private final int quarantined;

        MigrationReport(int scanned, int migrated, int quarantined) {
            this.scanned = scanned;
            this.migrated = migrated;
            this.quarantined = quarantined;
        }

        public int scanned() {
            return scanned;
        }

        public int migrated() {
            return migrated;
        }

        public int quarantined() {
            return quarantined;
        }
    }
}
