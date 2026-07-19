using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;

namespace PortalSecurity;

public static class PortalHost
{
    public static WebApplication Build(IServer server)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            EnvironmentName = "Testing",
        });
        builder.WebHost.UseServer(server);
        builder.Services.AddPortalSecurity();

        var app = builder.Build();
        app.UseRouting();
        app.UseAuthorization();
        app.UseAuthentication();

        app.MapGet("/reports", [Authorize(Policy = "reports.read")] () => "reports ready");
        app.MapGet("/operations", [Authorize(Policy = "operations")] () => "operations ready");
        return app;
    }
}
