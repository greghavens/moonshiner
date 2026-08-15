import java.nio.file.Path;

/** Minimal harness for the single-file architecture client. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain <estate-inventory> <compatibility-snapshot> <output-directory>");
        }
        ArchitectureClient.generate(Path.of(args[0]), Path.of(args[1]), Path.of(args[2]));
    }
}
