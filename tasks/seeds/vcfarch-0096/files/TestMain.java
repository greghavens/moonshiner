import java.nio.file.Path;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain <estate-inventory.json> <compatibility-snapshot.json>");
        }
        String artifact = MigrationArchitecture.create(Path.of(args[0]), Path.of(args[1]));
        if (artifact == null || artifact.isBlank()) {
            throw new AssertionError("MigrationArchitecture.create returned no artifact");
        }
        System.out.print(artifact);
    }
}
