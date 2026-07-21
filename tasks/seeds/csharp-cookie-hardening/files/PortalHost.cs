using System.Security.Claims;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace CedarPortal;

public static class PortalHost
{
    private const string LoginState = "cedar-login-state";

    public static WebApplication Build(IServer server, TimeProvider timeProvider)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            EnvironmentName = "Testing",
        });
        builder.WebHost.UseServer(server);
        builder.Services.AddSingleton(timeProvider);
        builder.Services.AddPortalCookies();
        builder.Services.AddOptions<CookieAuthenticationOptions>(
                CookieAuthenticationDefaults.AuthenticationScheme)
            .Configure(options => options.TimeProvider = timeProvider);
        builder.Services.AddAuthorization();

        var app = builder.Build();
        app.UseSession();
        app.UseAuthentication();
        app.UseAuthorization();

        app.MapGet("/login", (HttpContext context, string? returnUrl) =>
        {
            context.Session.SetString("login-state", LoginState);
            context.Session.SetString("return-url", LocalReturnUrl(returnUrl));
            return Results.Redirect(
                "https://identity.cedar.example/authorize?state=" + LoginState);
        });

        app.MapGet("/signin-oidc", async (HttpContext context, string? state) =>
        {
            if (!string.Equals(state, context.Session.GetString("login-state"),
                    StringComparison.Ordinal))
            {
                return Results.BadRequest("login state was not preserved");
            }

            var returnUrl = LocalReturnUrl(context.Session.GetString("return-url"));
            context.Session.Remove("login-state");
            context.Session.Remove("return-url");

            var identity = new ClaimsIdentity(
                [new Claim(ClaimTypes.Name, "cedar-user")],
                CookieAuthenticationDefaults.AuthenticationScheme);
            await context.SignInAsync(
                CookieAuthenticationDefaults.AuthenticationScheme,
                new ClaimsPrincipal(identity),
                new AuthenticationProperties { IsPersistent = true });
            return Results.Redirect(returnUrl);
        });

        app.MapGet("/account", [Authorize] (ClaimsPrincipal user) =>
            $"account:{user.Identity?.Name}");

        app.MapPost("/logout", [Authorize] async (HttpContext context, string? returnUrl) =>
        {
            await context.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
            context.Session.Clear();
            return Results.Redirect(LocalReturnUrl(returnUrl));
        });

        app.MapGet("/denied", () => Results.StatusCode(StatusCodes.Status403Forbidden));
        return app;
    }

    private static string LocalReturnUrl(string? candidate)
    {
        if (string.IsNullOrEmpty(candidate) || candidate[0] != '/')
            return "/";
        if (candidate.Length > 1 && (candidate[1] == '/' || candidate[1] == '\\'))
            return "/";
        return candidate;
    }
}
