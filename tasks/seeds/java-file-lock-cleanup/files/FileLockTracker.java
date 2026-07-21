import java.io.Closeable;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Opens archive owners and removes their staged files.
 *
 * <p>The interface is injectable so callers can reproduce operating systems that
 * reject deletion while a {@link ZipFile} still owns an open file handle.</p>
 */
public interface FileLockTracker {
    ArchiveOwner open(Path archive) throws IOException;

    void delete(Path archive) throws IOException;

    interface ArchiveOwner extends Closeable {
        InputStream openEntry(String entryName) throws IOException;
    }

    static FileLockTracker system() {
        return new SystemFileLockTracker();
    }
}

final class SystemFileLockTracker implements FileLockTracker {
    @Override
    public ArchiveOwner open(Path archive) throws IOException {
        ZipFile zipFile = new ZipFile(archive.toFile());
        return new ArchiveOwner() {
            @Override
            public InputStream openEntry(String entryName) throws IOException {
                ZipEntry entry = zipFile.getEntry(entryName);
                if (entry == null) {
                    throw new FileNotFoundException(
                            "archive entry not found: " + entryName);
                }
                return zipFile.getInputStream(entry);
            }

            @Override
            public void close() throws IOException {
                zipFile.close();
            }
        };
    }

    @Override
    public void delete(Path archive) throws IOException {
        Files.deleteIfExists(archive);
    }
}
