namespace Foundry.Telemetry;

/// <summary>Collects events in order; used by tests and local tooling.</summary>
public sealed class InMemorySink : ILogSink
{
    private readonly List<LogEvent> _events = new();

    public IReadOnlyList<LogEvent> Events => _events;

    public int FlushCount { get; private set; }

    public void Emit(LogEvent evt) => _events.Add(evt);

    public void Flush() => FlushCount++;
}
