package incident;

import java.util.Objects;

/** Applies a transfer in one database transaction. */
public final class FundsTransferService {
    @FunctionalInterface
    public interface ConnectionPool {
        DatabaseConnection borrow(String requestId) throws DatabaseException;
    }

    public interface DatabaseConnection extends AutoCloseable {
        void begin() throws DatabaseException;

        void debit(String account, long cents) throws DatabaseException;

        void credit(String account, long cents) throws DatabaseException;

        void commit() throws DatabaseException;

        void rollback() throws DatabaseException;

        @Override
        void close() throws DatabaseException;
    }

    public static final class DatabaseException extends Exception {
        private static final long serialVersionUID = 1L;

        public DatabaseException(String message) {
            super(message);
        }

        public DatabaseException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    public record Transfer(String debitAccount, String creditAccount, long cents) {
        public Transfer {
            requireAccount(debitAccount, "debitAccount");
            requireAccount(creditAccount, "creditAccount");
            if (debitAccount.equals(creditAccount)) {
                throw new IllegalArgumentException("accounts must differ");
            }
            if (cents <= 0) {
                throw new IllegalArgumentException("cents must be positive");
            }
        }

        private static void requireAccount(String account, String label) {
            Objects.requireNonNull(account, label);
            if (account.isBlank()) {
                throw new IllegalArgumentException(label + " must not be blank");
            }
        }
    }

    private final ConnectionPool pool;

    public FundsTransferService(ConnectionPool pool) {
        this.pool = Objects.requireNonNull(pool, "pool");
    }

    public void transfer(String requestId, Transfer transfer) throws DatabaseException {
        Objects.requireNonNull(requestId, "requestId");
        if (requestId.isBlank()) {
            throw new IllegalArgumentException("requestId must not be blank");
        }
        Objects.requireNonNull(transfer, "transfer");

        DatabaseConnection connection = pool.borrow(requestId);
        try {
            connection.begin();
            connection.debit(transfer.debitAccount(), transfer.cents());
            connection.credit(transfer.creditAccount(), transfer.cents());
            connection.commit();
        } catch (DatabaseException operationFailure) {
            try {
                connection.rollback();
            } catch (DatabaseException rollbackFailure) {
                operationFailure.addSuppressed(rollbackFailure);
            }
            throw operationFailure;
        }
        connection.close();
    }
}
