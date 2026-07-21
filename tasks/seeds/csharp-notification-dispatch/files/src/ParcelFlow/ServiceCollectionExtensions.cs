using Microsoft.Extensions.DependencyInjection;

namespace ParcelFlow;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddParcelFlow(this IServiceCollection services)
    {
        ArgumentNullException.ThrowIfNull(services);

        services.AddScoped<INotificationHandler<ShipmentReleased>, ReleaseAuditHandler>();
        services.AddScoped<INotificationHandler<ShipmentReleased>, CustomerUpdateHandler>();
        services.AddSingleton<NotificationRetryPolicy>();
        services.AddScoped<IShipmentReleaseService, ShipmentReleaseService>();

        return services;
    }
}
