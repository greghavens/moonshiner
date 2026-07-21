namespace TokenRefreshStorm;

public enum RefreshEventKind
{
    Started,
    Joined,
    Succeeded,
    Failed,
    Canceled,
    WaiterCanceled
}

public sealed record RefreshEvent(
    RefreshEventKind Kind,
    long RefreshId,
    int Participants,
    TimeSpan Duration,
    string? FailureType = null);

public interface IRefreshTelemetry
{
    void Record(RefreshEvent refreshEvent);
}

public sealed class NullRefreshTelemetry : IRefreshTelemetry
{
    public static NullRefreshTelemetry Instance { get; } = new();

    private NullRefreshTelemetry()
    {
    }

    public void Record(RefreshEvent refreshEvent)
    {
    }
}
