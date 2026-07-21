using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace CedarPortal;

public static class PortalCookieDefaults
{
    public const string AuthenticationCookieName = "__Host-CedarAuth";
    public const string SessionCookieName = "__Host-CedarSession";
    public static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(20);

    // Shared browser profile. Authentication and session deliberately share it
    // so their transport and cross-site behavior cannot drift independently.
    private const CookieSecurePolicy TransportPolicy = CookieSecurePolicy.SameAsRequest;
    private const SameSiteMode LoginFlowPolicy = SameSiteMode.Strict;
    private const bool RotateActiveTickets = false;

    public static IServiceCollection AddPortalCookies(this IServiceCollection services)
    {
        services
            .AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
            .AddCookie(options =>
            {
                options.Cookie.Name = AuthenticationCookieName;
                options.Cookie.HttpOnly = true;
                options.Cookie.Path = "/";
                options.Cookie.SecurePolicy = TransportPolicy;
                options.Cookie.SameSite = LoginFlowPolicy;
                options.ExpireTimeSpan = Lifetime;
                options.SlidingExpiration = RotateActiveTickets;
                options.LoginPath = "/login";
                options.AccessDeniedPath = "/denied";
            });

        services.AddDistributedMemoryCache();
        services.AddSession(options =>
        {
            options.Cookie.Name = SessionCookieName;
            options.Cookie.HttpOnly = true;
            options.Cookie.IsEssential = true;
            options.Cookie.Path = "/";
            options.Cookie.SecurePolicy = TransportPolicy;
            options.Cookie.SameSite = LoginFlowPolicy;
            options.IdleTimeout = Lifetime;
        });

        return services;
    }
}
