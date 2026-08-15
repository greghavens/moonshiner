import java.nio.file.Path;

/** Fixed harness: invokes the client and writes its architecture artifact. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException("expected design, inventory, and compatibility paths");
        }
        Path design = Path.of(args[0]);
        Path inventory = Path.of(args[1]);
        Path compatibility = Path.of(args[2]);
        System.out.print(ArchitectureClient.build(design, inventory, compatibility));
    }
}
