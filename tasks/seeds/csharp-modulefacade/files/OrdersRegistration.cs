using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace Checkout.Modules.Orders;

public static class OrdersRegistration
{
    public static IServiceCollection AddOrdersModule(this IServiceCollection services)
    {
        ArgumentNullException.ThrowIfNull(services);

        services.TryAddSingleton<OrdersModule>(provider => new OrdersModule(provider));
        return services;
    }
}
