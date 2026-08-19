import java.net.URI;
import java.util.List;
import java.util.Map;

/**
 * Minimal VCF Automation change client. Implement {@link #runChange} using only
 * the JDK standard library and the contract in docs/contract.json.
 */
public final class AutomationChangeClient {
    public record ChangeStep(String actionId, Map<String, Object> inputs, String reason) {}

    public record StepResult(String actionId, String requestId, String status, String details) {}

    private AutomationChangeClient() {}

    public static List<StepResult> runChange(
            URI apiBase,
            String bearerToken,
            String resourceId,
            List<ChangeStep> steps) throws Exception {
        throw new UnsupportedOperationException("Not implemented");
    }
}
