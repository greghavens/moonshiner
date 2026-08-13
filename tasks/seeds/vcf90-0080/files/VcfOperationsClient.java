import java.io.IOException;
import java.net.URI;

/**
 * Minimal client for the VCF Operations maintenance-schedule change exercised by TestMain.
 * No third-party JSON or HTTP dependency is required.
 */
public final class VcfOperationsClient {
    public record Schedule(
            int hour,
            int minuteOfTheHour,
            int duration,
            String scheduleType,
            String startDate,
            String expirationDate,
            String timeZone,
            Integer expireRuns) {
    }

    public record StepResult(
            String operationId,
            int statusCode,
            boolean succeeded,
            String resourceId,
            String responseBody) {
    }

    public record ChangeReport(StepResult create, StepResult update) {
    }

    public VcfOperationsClient(URI applianceUri, String token) {
        throw new UnsupportedOperationException("TODO");
    }

    public ChangeReport applyMaintenanceScheduleChange(
            String key, Schedule requested, Schedule replacement)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
