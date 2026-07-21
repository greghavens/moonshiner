package com.moonshiner.migration;

import java.util.List;

/** Storage boundary used by the migration job. */
public interface DocumentStore {
    long loadCheckpoint();

    List<Row> rowsAfter(long checkpoint);

    Transaction beginTransaction();

    final class Row {
        private final long id;
        private final String json;

        public Row(long id, String json) {
            this.id = id;
            this.json = json;
        }

        public long id() {
            return id;
        }

        public String json() {
            return json;
        }
    }

    interface Transaction {
        void replaceJson(long rowId, String json);

        void quarantine(long rowId, String reason);

        void saveCheckpoint(long rowId);

        void commit();

        void rollback();
    }
}
