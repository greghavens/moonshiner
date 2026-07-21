using MoonRelay.Configuration;

var tests = new (string Name, Action Run)[]
{
    ("sparse binding preserves initializer defaults", SparseBindingPreservesDefaults),
    ("deployment environment overrides appsettings", EnvironmentOverridesAppSettings),
    ("command line is the final explicit override", CommandLineIsFinalOverride),
    ("overlaps produce compatibility diagnostics", OverlapsProduceDiagnostics),
    ("non-overlapping sources are quiet", NonOverlappingSourcesAreQuiet),
    ("diagnostics have deterministic option ordering", DiagnosticsAreDeterministic)
};

var failures = new List<string>();
foreach (var test in tests)
{
    try
    {
        test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failures.Add(test.Name);
        Console.Error.WriteLine($"FAIL {test.Name}: {exception.Message}");
    }
}

if (failures.Count != 0)
{
    Console.Error.WriteLine($"{failures.Count} test(s) failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"All {tests.Length} tests passed.");
return 0;

static void SparseBindingPreservesDefaults()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(("Relay:QueueName", "archive")));

    Equal("archive", result.Options.QueueName, "configured queue");
    Equal(4, result.Options.MaxConcurrency, "initializer concurrency");
    Equal(30, result.Options.DrainTimeoutSeconds, "initializer timeout");
}

static void EnvironmentOverridesAppSettings()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(
            ("Relay:QueueName", "persistent"),
            ("Relay:MaxConcurrency", "4"),
            ("Relay:DrainTimeoutSeconds", "45")),
        environment: Values(
            ("Relay:QueueName", "priority"),
            ("Relay:MaxConcurrency", "12")));

    Equal("priority", result.Options.QueueName, "environment queue");
    Equal(12, result.Options.MaxConcurrency, "environment concurrency");
    Equal(45, result.Options.DrainTimeoutSeconds, "unshadowed appsettings timeout");
}

static void CommandLineIsFinalOverride()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(("Relay:MaxConcurrency", "4")),
        environment: Values(("Relay:MaxConcurrency", "12")),
        commandLine: Values(("Relay:MaxConcurrency", "8")));

    Equal(8, result.Options.MaxConcurrency, "command-line concurrency");
}

static void OverlapsProduceDiagnostics()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(("Relay:QueueName", "persistent")),
        environment: Values(("relay:queuename", "priority")),
        commandLine: Values(("RELAY:QUEUENAME", "emergency")));

    Equal("emergency", result.Options.QueueName, "case-insensitive selected value");
    Equal(2, result.Diagnostics.Count, "diagnostic count");

    CompatibilityDiagnostic appSettings = result.Diagnostics[0];
    Equal("CFG001", appSettings.Code, "diagnostic code");
    Equal("Relay:QueueName", appSettings.OptionPath, "canonical option path");
    Equal(ConfigurationSource.CommandLine, appSettings.SelectedSource, "selected source");
    Equal(ConfigurationSource.AppSettings, appSettings.ShadowedSource, "first shadowed source");
    Contains("CommandLine", appSettings.Message, "diagnostic selected-source name");
    Contains("AppSettings", appSettings.Message, "diagnostic shadowed-source name");

    CompatibilityDiagnostic environment = result.Diagnostics[1];
    Equal(ConfigurationSource.CommandLine, environment.SelectedSource, "final selected source");
    Equal(ConfigurationSource.Environment, environment.ShadowedSource, "second shadowed source");
    Contains("CommandLine", environment.Message, "second diagnostic selected-source name");
    Contains("Environment", environment.Message, "second diagnostic shadowed-source name");
}

static void NonOverlappingSourcesAreQuiet()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(("Relay:QueueName", "archive")),
        environment: Values(("Relay:MaxConcurrency", "6")),
        commandLine: Values(("Relay:DrainTimeoutSeconds", "10")));

    Equal(0, result.Diagnostics.Count, "diagnostic count");
    Equal("archive", result.Options.QueueName, "queue");
    Equal(6, result.Options.MaxConcurrency, "concurrency");
    Equal(10, result.Options.DrainTimeoutSeconds, "timeout");
}

static void DiagnosticsAreDeterministic()
{
    RelayOptionsResolution result = Resolve(
        appSettings: Values(
            ("Relay:QueueName", "one"),
            ("Relay:DrainTimeoutSeconds", "40")),
        environment: Values(
            ("Relay:QueueName", "two"),
            ("Relay:DrainTimeoutSeconds", "20")));

    Equal(2, result.Diagnostics.Count, "diagnostic count");
    Equal("Relay:DrainTimeoutSeconds", result.Diagnostics[0].OptionPath, "first option");
    Equal("Relay:QueueName", result.Diagnostics[1].OptionPath, "second option");
    Equal(ConfigurationSource.Environment, result.Diagnostics[0].SelectedSource, "two-layer selected source");
    Equal(ConfigurationSource.AppSettings, result.Diagnostics[0].ShadowedSource, "two-layer shadowed source");
}

static RelayOptionsResolution Resolve(
    IReadOnlyDictionary<string, string?>? appSettings = null,
    IReadOnlyDictionary<string, string?>? environment = null,
    IReadOnlyDictionary<string, string?>? commandLine = null)
{
    return RelayOptionsResolver.Resolve(
        new ConfigurationLayer(ConfigurationSource.AppSettings, appSettings ?? Values()),
        new ConfigurationLayer(ConfigurationSource.Environment, environment ?? Values()),
        new ConfigurationLayer(ConfigurationSource.CommandLine, commandLine ?? Values()));
}

static IReadOnlyDictionary<string, string?> Values(params (string Key, string? Value)[] values)
{
    return values.ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.OrdinalIgnoreCase);
}

static void Equal<T>(T expected, T actual, string label)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException(
            $"{label}: expected <{expected}> but found <{actual}>");
    }
}

static void Contains(string expectedFragment, string actual, string label)
{
    if (!actual.Contains(expectedFragment, StringComparison.Ordinal))
    {
        throw new InvalidOperationException(
            $"{label}: expected <{actual}> to contain <{expectedFragment}>");
    }
}
