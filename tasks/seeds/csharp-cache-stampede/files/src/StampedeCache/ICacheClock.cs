namespace StampedeCache;

public interface ICacheClock
{
    DateTimeOffset UtcNow { get; }
}

public sealed class SystemCacheClock : ICacheClock
{
    public static SystemCacheClock Instance { get; } = new();

    private SystemCacheClock()
    {
    }

    public DateTimeOffset UtcNow => DateTimeOffset.UtcNow;
}
