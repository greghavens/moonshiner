namespace ClockSkewIncident;

public interface ISystemClock
{
    DateTimeOffset UtcNow { get; }

    long GetTimestamp();

    TimeSpan GetElapsedTime(long startingTimestamp);
}

public readonly record struct DistributedLogTimestamps(
    DateTimeOffset ProducerTimestampUtc,
    DateTimeOffset ConsumerReceivedAtUtc);

public interface IRemoteClockSkew
{
    DateTimeOffset ToRemoteTime(DateTimeOffset localTimeUtc);
}

public sealed class DistributedLogClockSkew : IRemoteClockSkew
{
    private readonly TimeSpan _remoteMinusLocal;

    public DistributedLogClockSkew(DistributedLogTimestamps timestamps)
    {
        _remoteMinusLocal = timestamps.ProducerTimestampUtc - timestamps.ConsumerReceivedAtUtc;
    }

    public DateTimeOffset ToRemoteTime(DateTimeOffset localTimeUtc) =>
        localTimeUtc + _remoteMinusLocal;
}
