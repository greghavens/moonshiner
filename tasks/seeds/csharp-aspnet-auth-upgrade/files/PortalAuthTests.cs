using System.Text;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Primitives;

namespace PortalSecurity;

internal sealed record Exchange(int Status, string Body, string? Challenge);

internal sealed class CaptureServer : IServer
{
    private Func<IFeatureCollection, Task>? _dispatch;

    public IFeatureCollection Features { get; } = new FeatureCollection();
    public int Starts { get; private set; }
    public int Stops { get; private set; }

    public Task StartAsync<TContext>(
        IHttpApplication<TContext> application,
        CancellationToken cancellationToken) where TContext : notnull
    {
        Starts++;
        _dispatch = async features =>
        {
            var context = application.CreateContext(features);
            Exception? failure = null;
            try
            {
                await application.ProcessRequestAsync(context);
            }
            catch (Exception error)
            {
                failure = error;
                throw;
            }
            finally
            {
                application.DisposeContext(context, failure);
            }
        };
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken cancellationToken)
    {
        Stops++;
        return Task.CompletedTask;
    }

    public void Dispose() { }

    public async Task<Exchange> SendAsync(
        string path,
        IReadOnlyDictionary<string, string>? headers = null)
    {
        if (_dispatch is null) throw new InvalidOperationException("server is not started");
        var requestHeaders = new HeaderDictionary();
        if (headers is not null)
        {
            foreach (var pair in headers)
                requestHeaders[pair.Key] = new StringValues(pair.Value);
        }

        var request = new HttpRequestFeature
        {
            Method = "GET",
            Scheme = "http",
            Path = path,
            PathBase = "",
            QueryString = "",
            RawTarget = path,
            Protocol = "HTTP/1.1",
            Headers = requestHeaders,
        };
        var response = new HttpResponseFeature();
        await using var body = new MemoryStream();
        var features = new FeatureCollection();
        features.Set<IHttpRequestFeature>(request);
        features.Set<IHttpResponseFeature>(response);
        features.Set<IHttpResponseBodyFeature>(new StreamResponseBodyFeature(body));

        await _dispatch(features);
        body.Position = 0;
        using var reader = new StreamReader(body, Encoding.UTF8, leaveOpen: true);
        var text = await reader.ReadToEndAsync();
        return new Exchange(
            response.StatusCode,
            text,
            response.Headers.WWWAuthenticate.ToString() is { Length: > 0 } challenge
                ? challenge
                : null);
    }
}

public sealed class PortalAuthTests
{
    private static async Task<(WebApplication App, CaptureServer Server)> StartAsync()
    {
        var server = new CaptureServer();
        var app = PortalHost.Build(server);
        await app.StartAsync();
        return (app, server);
    }

    [Fact]
    public void AuthenticationMiddlewarePrecedesAuthorizationInPortalHost()
    {
        var sourcePath = Path.GetFullPath(Path.Combine(
            AppContext.BaseDirectory, "..", "..", "..", "PortalHost.cs"));
        var source = File.ReadAllText(sourcePath);
        const string authentication = "app.UseAuthentication();";
        const string authorization = "app.UseAuthorization();";
        var authenticationAt = source.IndexOf(authentication, StringComparison.Ordinal);
        var authorizationAt = source.IndexOf(authorization, StringComparison.Ordinal);

        Assert.True(authenticationAt >= 0, "PortalHost must install authentication middleware");
        Assert.True(authorizationAt > authenticationAt,
            "PortalHost must install authentication before authorization");
        Assert.Equal(authenticationAt, source.LastIndexOf(authentication, StringComparison.Ordinal));
        Assert.Equal(authorizationAt, source.LastIndexOf(authorization, StringComparison.Ordinal));
    }

    [Fact]
    public async Task AuthenticationPopulatesBothSchemePrincipalsBeforeAuthorizationRuns()
    {
        var (app, server) = await StartAsync();
        try
        {
            var report = await server.SendAsync("/reports", new Dictionary<string, string>
            {
                ["X-Portal-User"] = "pipeline-user",
                ["X-Portal-Scopes"] = "reports.read",
            });
            var operations = await server.SendAsync("/operations", new Dictionary<string, string>
            {
                ["X-Service-Identity"] = "pipeline-service",
            });
            Assert.Equal(new Exchange(200, "reports ready", null), report);
            Assert.Equal(new Exchange(200, "operations ready", null), operations);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
    }

    [Fact]
    public async Task IntegrationHostStartsAndStopsWithInjectedServer()
    {
        var (app, server) = await StartAsync();
        try
        {
            Assert.Equal(1, server.Starts);
            Assert.Empty(server.Features.Get<IServerAddressesFeature>()?.Addresses ?? []);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
        Assert.Equal(1, server.Stops);
    }

    [Fact]
    public async Task DefaultReportChallengeKeepsPortalSchemeAndHeader()
    {
        var (app, server) = await StartAsync();
        try
        {
            var exchange = await server.SendAsync("/reports");
            Assert.Equal(401, exchange.Status);
            Assert.Equal("PortalHeader realm=\"reports\"", exchange.Challenge);
            Assert.Equal("", exchange.Body);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
    }

    [Fact]
    public async Task ReportPolicyRequiresItsScopeAndPreservesForbidBehavior()
    {
        var (app, server) = await StartAsync();
        try
        {
            var allowed = await server.SendAsync("/reports", new Dictionary<string, string>
            {
                ["X-Portal-User"] = "fixture-user",
                ["X-Portal-Scopes"] = "profile.read, reports.read",
            });
            Assert.Equal(new Exchange(200, "reports ready", null), allowed);

            var forbidden = await server.SendAsync("/reports", new Dictionary<string, string>
            {
                ["X-Portal-User"] = "fixture-user",
                ["X-Portal-Scopes"] = "profile.read",
            });
            Assert.Equal(403, forbidden.Status);
            Assert.Null(forbidden.Challenge);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
    }

    [Fact]
    public async Task OperationsPolicySelectsOnlyServiceKeyScheme()
    {
        var (app, server) = await StartAsync();
        try
        {
            var allowed = await server.SendAsync("/operations", new Dictionary<string, string>
            {
                ["X-Service-Identity"] = "fixture-service",
            });
            Assert.Equal(new Exchange(200, "operations ready", null), allowed);

            var wrongScheme = await server.SendAsync("/operations", new Dictionary<string, string>
            {
                ["X-Portal-User"] = "fixture-user",
                ["X-Portal-Scopes"] = "reports.read",
            });
            Assert.Equal(401, wrongScheme.Status);
            Assert.Equal("ServiceKey realm=\"operations\"", wrongScheme.Challenge);
        }
        finally
        {
            await app.StopAsync();
            await app.DisposeAsync();
        }
    }

    [Fact]
    public async Task RegistrationExposesStableDefaultSchemeSelection()
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddPortalSecurity();
        await using var provider = services.BuildServiceProvider();
        var schemes = provider.GetRequiredService<IAuthenticationSchemeProvider>();
        Assert.Equal(
            PortalSchemes.PortalHeader,
            (await schemes.GetDefaultAuthenticateSchemeAsync())?.Name);
        Assert.Equal(
            PortalSchemes.PortalHeader,
            (await schemes.GetDefaultChallengeSchemeAsync())?.Name);
        Assert.NotNull(await schemes.GetSchemeAsync(PortalSchemes.ServiceKey));
    }

    [Fact]
    public void ProtectedMigrationNotesRecordTheCurrentContract()
    {
        var notes = File.ReadAllText("contracts/aspnet_auth_migration.md");
        foreach (var phrase in new[]
        {
            "registered through `AddAuthentication`",
            "explicit default authenticate and challenge schemes",
            "`operations` policy selects only the service-key scheme",
            "`UseAuthentication` precedes `UseAuthorization`",
            "without opening a real socket",
        })
            Assert.Contains(phrase, notes, StringComparison.Ordinal);
    }
}
