using Microsoft.Extensions.DependencyInjection;

namespace Checkout.Modules.Orders;

public sealed class OrdersModule
{
    private readonly IServiceProvider _rootServices;

    public OrdersModule(IServiceProvider rootServices)
    {
        _rootServices = rootServices ?? throw new ArgumentNullException(nameof(rootServices));
    }

    public async Task<OrderReceipt> PlaceAsync(
        PlaceOrder command,
        CancellationToken cancellationToken = default)
    {
        Validate(command);

        var sequence = _rootServices.GetRequiredService<IOrderNumberSequence>();
        var store = _rootServices.GetRequiredService<IOrderStore>();
        var discount = _rootServices.GetService<IOrderDiscount>();
        var publisher = _rootServices.GetRequiredService<IEventPublisher>();

        var number = sequence.Next();
        var total = discount?.Apply(command.Sku, command.UnitPrice) ?? command.UnitPrice;
        var placed = new OrderPlaced(number, command.Sku, total, sequence.ScopeId);

        await store.SaveAsync(placed, cancellationToken).ConfigureAwait(false);
        await publisher.PublishAsync(placed, cancellationToken).ConfigureAwait(false);

        return new OrderReceipt(number, total, sequence.ScopeId);
    }

    private static void Validate(PlaceOrder command)
    {
        ArgumentNullException.ThrowIfNull(command);

        if (string.IsNullOrWhiteSpace(command.Sku))
        {
            throw new ArgumentException("SKU is required.", nameof(command));
        }

        if (command.UnitPrice < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(command), "Unit price cannot be negative.");
        }
    }
}
