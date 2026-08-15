import java.nio.file.Path;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <inventory.json> <compatibility-snapshot.json>");
        }
        System.out.print(ArchitectureClient.createArtifact(Path.of(args[0]), Path.of(args[1])));
    }
}
