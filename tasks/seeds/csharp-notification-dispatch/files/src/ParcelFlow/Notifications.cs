namespace ParcelFlow;

public sealed record ShipmentReleased(string ShipmentId);

public interface INotificationHandler<in TNotification>
{
    Task HandleAsync(TNotification notification, CancellationToken cancellationToken);
}

public sealed class TransientNotificationException : Exception
{
    public TransientNotificationException(string message)
        : base(message)
    {
    }
}
