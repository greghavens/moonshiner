import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.Objects;

/** Streams one entry from a staged zip and removes the stage when the stream closes. */
public final class BundleStreamer {
    private final FileLockTracker lockTracker;

    public BundleStreamer() {
        this(FileLockTracker.system());
    }

    public BundleStreamer(FileLockTracker lockTracker) {
        this.lockTracker = Objects.requireNonNull(lockTracker, "lockTracker");
    }

    /**
     * Returns a lazy stream for {@code entryName}. Closing it closes all resources
     * opened for the request and then deletes {@code stagedArchive}.
     */
    public InputStream open(Path stagedArchive, String entryName) throws IOException {
        Objects.requireNonNull(stagedArchive, "stagedArchive");
        Objects.requireNonNull(entryName, "entryName");

        FileLockTracker.ArchiveOwner owner = lockTracker.open(stagedArchive);
        try {
            InputStream entry = owner.openEntry(entryName);
            return new ManagedEntryStream(entry, owner, stagedArchive, lockTracker);
        } catch (Throwable openFailure) {
            Throwable failure = attempt(owner::close, openFailure);
            failure = attempt(() -> lockTracker.delete(stagedArchive), failure);
            rethrow(failure);
            throw new AssertionError("unreachable");
        }
    }

    @FunctionalInterface
    private interface CleanupAction {
        void run() throws IOException;
    }

    private static Throwable attempt(CleanupAction action, Throwable failure) {
        try {
            action.run();
        } catch (Throwable cleanupFailure) {
            if (failure == null) {
                return cleanupFailure;
            }
            if (failure != cleanupFailure) {
                failure.addSuppressed(cleanupFailure);
            }
        }
        return failure;
    }

    private static void rethrow(Throwable failure) throws IOException {
        if (failure instanceof IOException ioFailure) {
            throw ioFailure;
        }
        if (failure instanceof RuntimeException runtimeFailure) {
            throw runtimeFailure;
        }
        if (failure instanceof Error error) {
            throw error;
        }
        throw new IOException(failure);
    }

    private static final class ManagedEntryStream extends FilterInputStream {
        private final FileLockTracker.ArchiveOwner owner;
        private final Path stagedArchive;
        private final FileLockTracker lockTracker;
        private boolean closed;

        private ManagedEntryStream(
                InputStream entry,
                FileLockTracker.ArchiveOwner owner,
                Path stagedArchive,
                FileLockTracker lockTracker) {
            super(entry);
            this.owner = owner;
            this.stagedArchive = stagedArchive;
            this.lockTracker = lockTracker;
        }

        @Override
        public void close() throws IOException {
            if (closed) {
                return;
            }
            closed = true;

            Throwable failure = null;
            failure = attempt(in::close, failure);
            failure = attempt(() -> lockTracker.delete(stagedArchive), failure);
            if (failure != null) {
                rethrow(failure);
            }
        }
    }
}
