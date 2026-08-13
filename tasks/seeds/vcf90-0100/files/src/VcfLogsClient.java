import java.io.IOException;
import java.net.URI;
import java.util.List;
import java.util.Map;

/**
 * Minimal dependency-free client surface used by the TestMain harness.
 * Implement this file without changing the public API below.
 */
public final class VcfLogsClient {
    public record ForwarderPatch(
            String name,
            String host,
            Integer port,
            String protocol,
            Boolean sslEnabled,
            Integer workerCount,
            Integer diskCacheSize,
            Map<String, String> tags,
            String filter,
            String transportProtocol,
            Boolean forwardComplementaryFields,
            Boolean testConnection) {
    }

    public record ForwarderChange(String id, ForwarderPatch patch) {
    }

    public record ChangeResult(
            String id,
            boolean success,
            int httpStatus,
            String errorCode,
            String message) {
    }

    public record ChangeReport(List<ChangeResult> results) {
    }

    public VcfLogsClient(
            URI applianceBaseUri,
            String username,
            String password,
            String provider) {
        throw new UnsupportedOperationException("Not implemented");
    }

    public ChangeReport applyForwarderChanges(List<ForwarderChange> changes)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }
}
