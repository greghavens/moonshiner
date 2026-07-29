import java.io.IOException;
import java.nio.file.Path;
import java.util.List;

/**
 * Dependency-free VCF 9.1 NSX Policy client.
 *
 * <p>Implement this file only. The public API below is exercised by TestMain.
 */
public final class NsxPolicyClient {
    public record GroupSpec(
            String displayName,
            List<String> ipAddresses,
            String description) {
    }

    public record PolicySpec(
            String displayName,
            String ruleDisplayName,
            String destinationGroupPath,
            int sequenceNumber,
            int ruleSequenceNumber,
            String description,
            String ruleNotes) {
    }

    public record ChangePlan(GroupSpec group, PolicySpec policy) {
    }

    public record StepResult(
            String name,
            String operationId,
            String status,
            Integer httpStatus,
            Long errorCode) {
    }

    public record ChangeReport(
            String status,
            int succeeded,
            int failed,
            List<StepResult> steps) {
    }

    public NsxPolicyClient(
            String managerBaseUrl,
            String username,
            String password) {
        // TODO: validate and retain connection settings.
    }

    public ChangeReport applyChange(
            String domainId,
            String groupId,
            String securityPolicyId,
            ChangePlan plan,
            Path reportPath) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
