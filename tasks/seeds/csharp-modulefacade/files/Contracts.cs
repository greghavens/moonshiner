namespace Checkout.Modules.Orders;

public sealed record PlaceOrder(string Sku, decimal UnitPrice);

public sealed record OrderReceipt(int Number, decimal Total, int ScopeId);

public sealed record OrderPlaced(int Number, string Sku, decimal Total, int ScopeId);

public interface IOrderNumberSequence
{
    int ScopeId { get; }

    int Next();
}

public interface IOrderStore
{
    int ScopeId { get; }

    Task SaveAsync(OrderPlaced order, CancellationToken cancellationToken = default);
}

public interface IOrderDiscount
{
    decimal Apply(string sku, decimal unitPrice);
}

public interface IEventPublisher
{
    Task PublishAsync<TEvent>(TEvent @event, CancellationToken cancellationToken = default);
}

// This is the complete capability exposed by the Orders module's infrastructure boundary.
public interface IOrdersModuleFacade
{
    Task<OrderReceipt> PlaceAsync(
        PlaceOrder command,
        CancellationToken cancellationToken = default);
}
