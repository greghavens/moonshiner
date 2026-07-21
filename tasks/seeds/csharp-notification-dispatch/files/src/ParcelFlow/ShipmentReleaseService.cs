namespace ParcelFlow;

internal sealed class ShipmentReleaseService : IShipmentReleaseService
{
    private readonly IShipmentTransaction _transaction;
    private readonly IShipmentRepository _repository;
    private readonly IReadOnlyList<INotificationHandler<ShipmentReleased>> _handlers;
    private readonly NotificationRetryPolicy _retryPolicy;

    public ShipmentReleaseService(
        IShipmentTransaction transaction,
        IShipmentRepository repository,
        IEnumerable<INotificationHandler<ShipmentReleased>> handlers,
        NotificationRetryPolicy retryPolicy)
    {
        _transaction = transaction ?? throw new ArgumentNullException(nameof(transaction));
        _repository = repository ?? throw new ArgumentNullException(nameof(repository));
        ArgumentNullException.ThrowIfNull(handlers);
        _handlers = handlers.ToArray();
        _retryPolicy = retryPolicy ?? throw new ArgumentNullException(nameof(retryPolicy));
    }

    public async Task ReleaseAsync(
        string shipmentId,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(shipmentId);

        await _transaction.ExecuteAsync(
            token => _repository.MarkReleasedAsync(shipmentId, token),
            cancellationToken).ConfigureAwait(false);

        var notification = new ShipmentReleased(shipmentId);
        foreach (var handler in _handlers)
        {
            await _retryPolicy.DeliverAsync(
                handler,
                notification,
                cancellationToken).ConfigureAwait(false);
        }
    }
}
