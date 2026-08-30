import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import javax.tools.ToolProvider;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        Path root = Path.of(".").toAbsolutePath().normalize();
        List<String> sources;
        try (var paths = Files.walk(root)) {
            sources = paths.filter(Files::isRegularFile)
                    .filter(path -> path.toString().endsWith(".java"))
                    .filter(path -> !path.getFileName().toString().equals("TestMain.java"))
                    .filter(path -> !path.toString().matches(
                            ".*[\\\\/](tests|test|verify|verifier|verification|harness|mock|mocks)[\\\\/].*"))
                    .map(Path::toString).toList();
        }
        if (sources.isEmpty()) throw new IllegalStateException("No Java implementation sources found.");
        Path output = Files.createTempDirectory("vcf-local-check-");
        var arguments = new java.util.ArrayList<String>();
        arguments.add("-d");
        arguments.add(output.toString());
        arguments.addAll(sources);
        int status = ToolProvider.getSystemJavaCompiler().run(
                null, System.out, System.err, arguments.toArray(String[]::new));
        if (status != 0) System.exit(status);
        System.out.println("Local checks passed (" + sources.size() + " Java source files).");
    }
}
