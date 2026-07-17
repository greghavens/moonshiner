namespace Foundry.Telemetry;

/// <summary>
/// Fans events out to registered sinks, each with its own minimum level.
/// </summary>
public sealed class LogRouter
{
    private readonly List<(ILogSink Sink, string MinLevel)> _routes = new();

    public LogRouter Route(ILogSink sink, string minLevel)
    {
        LogLevels.Rank(minLevel); // validate eagerly
        _routes.Add((sink, minLevel));
        return this;
    }

    public void Publish(LogEvent evt)
    {
        foreach (var (sink, minLevel) in _routes)
        {
            if (LogLevels.AtLeast(evt.Level, minLevel))
            {
                sink.Emit(evt);
            }
        }
    }

    public void FlushAll()
    {
        foreach (var (sink, _) in _routes)
        {
            sink.Flush();
        }
    }
}
