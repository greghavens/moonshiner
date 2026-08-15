import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Fixed exercise harness for the single-file client. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        boolean architectureOnly = args.length == 1 && "--architecture-only".equals(args[0]);
        if (args.length > 1 || (args.length == 1 && !architectureOnly)) {
            throw new IllegalArgumentException("usage: TestMain [--architecture-only]");
        }

        String architecture = Main.architectureJson();
        if (architecture == null) {
            throw new IllegalStateException("Main.architectureJson() returned null");
        }

        Path outputDirectory = Path.of("build");
        Files.createDirectories(outputDirectory);
        Files.writeString(
                outputDirectory.resolve("architecture.json"),
                architecture,
                StandardCharsets.UTF_8);
        if (!architectureOnly) {
            String research = Main.researchConsultedJson();
            if (research == null) {
                throw new IllegalStateException("Main.researchConsultedJson() returned null");
            }
            Files.writeString(
                    outputDirectory.resolve("research-consulted.json"),
                    research,
                    StandardCharsets.UTF_8);
            System.out.println("Wrote architecture and research records");
        } else {
            System.out.println("Wrote architecture record");
        }
    }
}
