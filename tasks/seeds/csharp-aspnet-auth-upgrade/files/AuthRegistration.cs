using Microsoft.Extensions.DependencyInjection;

namespace PortalSecurity;

public sealed record LegacyAuthenticationSnapshot(
    string DefaultScheme,
    IReadOnlyList<string> Schemes,
    IReadOnlyList<string> Policies);

public static class AuthRegistration
{
    public static IServiceCollection AddPortalSecurity(this IServiceCollection services)
    {
        services.AddSingleton(new LegacyAuthenticationSnapshot(
            PortalSchemes.PortalHeader,
            [PortalSchemes.PortalHeader, PortalSchemes.ServiceKey],
            ["reports.read", "operations"]));
        return services;
    }
}

