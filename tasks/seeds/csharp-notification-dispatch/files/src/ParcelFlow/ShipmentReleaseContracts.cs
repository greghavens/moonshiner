namespace ParcelFlow;

public interface IShipmentReleaseService
{
    Task ReleaseAsync(string shipmentId, CancellationToken cancellationToken = default);
}

public interface IShipmentTransaction
{
    Task ExecuteAsync(
        Func<CancellationToken, Task> operation,
        CancellationToken cancellationToken);
}

public interface IShipmentRepository
{
    Task MarkReleasedAsync(string shipmentId, CancellationToken cancellationToken);
}
