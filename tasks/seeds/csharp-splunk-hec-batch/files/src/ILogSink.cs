namespace Foundry.Telemetry;

/// <summary>A destination for structured log events.</summary>
public interface ILogSink
{
    /// <summary>Accepts one event; may buffer.</summary>
    void Emit(LogEvent evt);

    /// <summary>Delivers anything buffered.</summary>
    void Flush();
}

public static class LogLevels
{
    private static readonly string[] Order = { "debug", "info", "warn", "error" };

    public static int Rank(string level)
    {
        int rank = Array.IndexOf(Order, level.ToLowerInvariant());
        return rank < 0
            ? throw new ArgumentException($"unknown log level '{level}'")
            : rank;
    }

    public static bool AtLeast(string level, string minimum) =>
        Rank(level) >= Rank(minimum);
}
