import java.io.IOException;
import java.net.http.HttpClient;
import java.util.List;

/** A small client for the VCF Automation project-change operations in docs/contract.json. */
public final class VcfAutomationClient {
    private final String baseUrl;
    private final String bearerToken;
    private final String apiVersion;
    private final HttpClient httpClient;

    public VcfAutomationClient(String baseUrl, String bearerToken, String apiVersion) {
        this(baseUrl, bearerToken, apiVersion, HttpClient.newHttpClient());
    }

    public VcfAutomationClient(
            String baseUrl, String bearerToken, String apiVersion, HttpClient httpClient) {
        this.baseUrl = baseUrl.endsWith("/")
                ? baseUrl.substring(0, baseUrl.length() - 1)
                : baseUrl;
        this.bearerToken = bearerToken;
        this.apiVersion = apiVersion;
        this.httpClient = httpClient;
    }

    public enum State {
        SUCCEEDED,
        ACCEPTED,
        FAILED
    }

    public record ZoneAssignment(
            String zoneId,
            Integer priority,
            Long maxNumberInstances,
            Long memoryLimitMB,
            Long cpuLimit,
            Long storageLimitGB) {}

    public record Tag(String key, String value) {}

    public record ProjectChange(
            String projectId,
            String name,
            String description,
            Boolean validatePrincipals,
            List<ZoneAssignment> zoneAssignments,
            List<Tag> resourceTags) {}

    public record StepResult(
            String operation,
            int statusCode,
            State state,
            String requestId,
            String remoteStatus,
            String messageId,
            String message) {}

    public record ChangeReport(List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }

        public boolean successful() {
            return steps.size() == 3
                    && steps.stream().noneMatch(step -> step.state() == State.FAILED);
        }
    }

    public ChangeReport applyProjectChange(ProjectChange change)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
