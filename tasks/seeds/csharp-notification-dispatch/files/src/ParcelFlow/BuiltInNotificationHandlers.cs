namespace ParcelFlow;

public sealed class ReleaseAuditHandler : INotificationHandler<ShipmentReleased>
{
    public Task HandleAsync(
        ShipmentReleased notification,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(notification);
        cancellationToken.ThrowIfCancellationRequested();
        return Task.CompletedTask;
    }
}

public sealed class CustomerUpdateHandler : INotificationHandler<ShipmentReleased>
{
    public Task HandleAsync(
        ShipmentReleased notification,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(notification);
        cancellationToken.ThrowIfCancellationRequested();
        return Task.CompletedTask;
    }
}
