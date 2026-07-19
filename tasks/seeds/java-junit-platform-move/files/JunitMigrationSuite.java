import java.util.List;

/** Platform plan used by the repository's single acceptance entry point. */
public final class JunitMigrationSuite {
    public List<PlatformContract.CaseResult> execute() {
        return new PlatformContract.Launcher(
                List.of(CurrentPricingTests.class),
                null
        ).execute();
    }
}

