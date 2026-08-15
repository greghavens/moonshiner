import java.nio.file.Path;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain <estate-inventory.json> <compatibility-snapshot.json>");
        }
        String artifact = DesignClient.buildArchitecture(Path.of(args[0]), Path.of(args[1]));
        if (artifact == null) {
            throw new IllegalStateException("DesignClient returned null");
        }
        System.out.print(artifact);
    }
}
