using System.Text;
using CedarPortal;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;
using Microsoft.Extensions.Primitives;

var tests = new (string Name, Func<Task> Run)[]
{
    ("resolved cookie and session options are hardened", Tests.ResolvedOptionsAreHardened),
    ("HTTP login still emits Secure host cookies", Tests.HttpLoginEmitsSecureCookie),
    ("top-level cross-site callback preserves login and local redirect", Tests.CrossSiteLoginRoundTrip),
    ("external return URLs remain rejected", Tests.ExternalReturnUrlIsRejected),
    ("active authentication ticket rotates after half-life", Tests.ActiveTicketRotates),
    ("logout expires the hardened cookie and protects account", Tests.LogoutExpiresHardenedCookie),
    ("integration host uses only the injected server", Tests.HostUsesInjectedServer),
};

var failures = 0;
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception error)
    {
        failures++;
        Console.Error.WriteLine($"FAIL {test.Name}: {error.Message}");
    }
}

if (failures != 0)
{
    Console.Error.WriteLine($"{failures} protected test(s) failed");
    return 1;
}

Console.WriteLine("All protected cookie-hardening tests passed");
return 0;

internal static class Tests
{
    private static readonly DateTimeOffset Epoch =
        new(2031, 4, 5, 12, 0, 0, TimeSpan.Zero);

    public static async Task ResolvedOptionsAreHardened()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var auth = fixture.App.Services
            .GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>()
            .Get(CookieAuthenticationDefaults.AuthenticationScheme);
        var session = fixture.App.Services.GetRequiredService<IOptions<SessionOptions>>().Value;

        Check.Equal(CookieSecurePolicy.Always, auth.Cookie.SecurePolicy,
            "authentication cookie transport policy");
        Check.Equal(SameSiteMode.Lax, auth.Cookie.SameSite,
            "authentication cookie SameSite policy");
        Check.True(auth.Cookie.HttpOnly, "authentication cookie must be HttpOnly");
        Check.Equal("/", auth.Cookie.Path, "authentication cookie path");
        Check.Equal<string?>(null, auth.Cookie.Domain, "authentication cookie must be host-only");
        Check.True(auth.Cookie.Name?.StartsWith("__Host-", StringComparison.Ordinal) == true,
            "authentication cookie must retain the __Host- prefix");
        Check.True(auth.SlidingExpiration, "active tickets must use sliding renewal");
        Check.Equal(PortalCookieDefaults.Lifetime, auth.ExpireTimeSpan,
            "authentication lifetime");

        Check.Equal(CookieSecurePolicy.Always, session.Cookie.SecurePolicy,
            "session cookie transport policy");
        Check.Equal(SameSiteMode.Lax, session.Cookie.SameSite,
            "session cookie SameSite policy");
        Check.True(session.Cookie.HttpOnly, "session cookie must be HttpOnly");
        Check.True(session.Cookie.IsEssential, "login session cookie must remain essential");
        Check.Equal("/", session.Cookie.Path, "session cookie path");
        Check.Equal<string?>(null, session.Cookie.Domain, "session cookie must be host-only");
        Check.True(session.Cookie.Name?.StartsWith("__Host-", StringComparison.Ordinal) == true,
            "session cookie must retain the __Host- prefix");
        Check.Equal(PortalCookieDefaults.Lifetime, session.IdleTimeout, "session idle timeout");
    }

    public static async Task HttpLoginEmitsSecureCookie()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var login = await fixture.Server.SendAsync("GET", "http", "/login?returnUrl=%2Faccount");
        Check.Equal(302, login.Status, "login status");
        var session = Cookies.RequiredSetCookie(login, PortalCookieDefaults.SessionCookieName);
        Cookies.AssertHardened(session, SameSiteMode.Lax);
    }

    public static async Task CrossSiteLoginRoundTrip()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var login = await fixture.Server.SendAsync("GET", "https", "/login?returnUrl=%2Faccount");
        Check.Equal("https://identity.cedar.example/authorize?state=cedar-login-state",
            login.Location, "identity-provider redirect");

        var sessionLine = Cookies.RequiredSetCookie(login, PortalCookieDefaults.SessionCookieName);
        Cookies.AssertHardened(sessionLine, SameSiteMode.Lax);
        var callbackCookie = Cookies.ForCrossSiteTopLevelGet(sessionLine);
        Check.True(callbackCookie is not null,
            "the portal session must be sent on the top-level identity-provider return");

        var callback = await fixture.Server.SendAsync(
            "GET", "https", "/signin-oidc?state=cedar-login-state",
            Cookies.Header(callbackCookie!));
        Check.Equal(302, callback.Status, "callback status");
        Check.Equal("/account", callback.Location, "remembered local redirect");

        var authLine = Cookies.RequiredSetCookie(callback, PortalCookieDefaults.AuthenticationCookieName);
        Cookies.AssertHardened(authLine, SameSiteMode.Lax);
        var account = await fixture.Server.SendAsync(
            "GET", "https", "/account", Cookies.Header(Cookies.Pair(authLine)));
        Check.Equal(200, account.Status, "authenticated account status");
        Check.Equal("account:cedar-user", account.Body, "authenticated account body");
    }

    public static async Task ExternalReturnUrlIsRejected()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var login = await fixture.Server.SendAsync(
            "GET", "https", "/login?returnUrl=https%3A%2F%2Fevil.example%2Fsteal");
        var session = Cookies.RequiredSetCookie(login, PortalCookieDefaults.SessionCookieName);
        var callback = await fixture.Server.SendAsync(
            "GET", "https", "/signin-oidc?state=cedar-login-state",
            Cookies.Header(Cookies.Pair(session)));
        Check.Equal(302, callback.Status, "callback status");
        Check.Equal("/", callback.Location, "unsafe return URL fallback");
    }

    public static async Task ActiveTicketRotates()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var login = await CompleteLogin(fixture, "/account");
        var originalLine = Cookies.RequiredSetCookie(login, PortalCookieDefaults.AuthenticationCookieName);
        var originalPair = Cookies.Pair(originalLine);

        fixture.Clock.Advance(TimeSpan.FromMinutes(11));
        var account = await fixture.Server.SendAsync(
            "GET", "https", "/account", Cookies.Header(originalPair));
        Check.Equal(200, account.Status, "account status after half-life");
        var renewedLine = Cookies.RequiredSetCookie(account, PortalCookieDefaults.AuthenticationCookieName);
        Cookies.AssertHardened(renewedLine, SameSiteMode.Lax);
        Check.NotEqual(originalPair, Cookies.Pair(renewedLine),
            "sliding renewal must rotate the protected authentication value");
    }

    public static async Task LogoutExpiresHardenedCookie()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        var login = await CompleteLogin(fixture, "/account");
        var authLine = Cookies.RequiredSetCookie(login, PortalCookieDefaults.AuthenticationCookieName);
        var logout = await fixture.Server.SendAsync(
            "POST", "http", "/logout?returnUrl=%2Fsigned-out",
            Cookies.Header(Cookies.Pair(authLine)));

        Check.Equal(302, logout.Status, "logout status");
        Check.Equal("/signed-out", logout.Location, "logout local redirect");
        var deletion = Cookies.RequiredSetCookie(logout, PortalCookieDefaults.AuthenticationCookieName);
        Cookies.AssertHardened(deletion, SameSiteMode.Lax);
        Check.Equal("", Cookies.Value(deletion), "logout cookie value");
        Check.True(Cookies.HasExpiredDate(deletion, Epoch),
            "logout must expire the authentication cookie in the past");

        var account = await fixture.Server.SendAsync("GET", "https", "/account");
        Check.Equal(302, account.Status, "cleared browser account status");
        Check.True(Uri.TryCreate(account.Location, UriKind.Absolute, out var challenge),
            $"cleared browser login redirect must be absolute, got <{account.Location}>");
        Check.Equal("portal.cedar.example", challenge!.Host,
            "cleared browser login redirect host");
        Check.Equal("/login", challenge.AbsolutePath,
            "cleared browser login redirect path");
        Check.True(challenge.Query.Contains("ReturnUrl=%2Faccount", StringComparison.Ordinal),
            $"cleared browser login redirect must remember /account, got <{account.Location}>");
    }

    public static async Task HostUsesInjectedServer()
    {
        await using var fixture = await Fixture.StartAsync(Epoch);
        Check.Equal(1, fixture.Server.Starts, "in-memory server start count");
        Check.Equal(0,
            fixture.Server.Features.Get<IServerAddressesFeature>()?.Addresses.Count ?? 0,
            "published listener address count");
        await fixture.StopAsync();
        Check.Equal(1, fixture.Server.Stops, "in-memory server stop count");
    }

    private static async Task<Exchange> CompleteLogin(Fixture fixture, string returnUrl)
    {
        var login = await fixture.Server.SendAsync(
            "GET", "https", "/login?returnUrl=" + Uri.EscapeDataString(returnUrl));
        var session = Cookies.RequiredSetCookie(login, PortalCookieDefaults.SessionCookieName);
        return await fixture.Server.SendAsync(
            "GET", "https", "/signin-oidc?state=cedar-login-state",
            Cookies.Header(Cookies.Pair(session)));
    }
}

internal sealed class Fixture : IAsyncDisposable
{
    private bool _stopped;

    private Fixture(WebApplication app, CaptureServer server, ManualTimeProvider clock)
    {
        App = app;
        Server = server;
        Clock = clock;
    }

    public WebApplication App { get; }
    public CaptureServer Server { get; }
    public ManualTimeProvider Clock { get; }

    public static async Task<Fixture> StartAsync(DateTimeOffset now)
    {
        var server = new CaptureServer();
        var clock = new ManualTimeProvider(now);
        var app = PortalHost.Build(server, clock);
        await app.StartAsync();
        return new Fixture(app, server, clock);
    }

    public async Task StopAsync()
    {
        if (_stopped) return;
        _stopped = true;
        await App.StopAsync();
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync();
        await App.DisposeAsync();
    }
}

internal sealed class ManualTimeProvider(DateTimeOffset now) : TimeProvider
{
    private DateTimeOffset _now = now;
    public override DateTimeOffset GetUtcNow() => _now;
    public void Advance(TimeSpan amount) => _now += amount;
}

internal sealed record Exchange(
    int Status,
    string Body,
    string? Location,
    IReadOnlyList<string> SetCookies);

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
                if (features.Get<IHttpResponseFeature>() is CaptureResponseFeature response)
                    await response.FireOnStartingAsync();
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
        string method,
        string scheme,
        string target,
        IReadOnlyDictionary<string, string>? headers = null)
    {
        if (_dispatch is null) throw new InvalidOperationException("server is not started");
        var uri = new Uri("https://portal.cedar.example" + target);
        var requestHeaders = new HeaderDictionary
        {
            ["Host"] = uri.Authority,
        };
        if (headers is not null)
        {
            foreach (var pair in headers)
                requestHeaders[pair.Key] = new StringValues(pair.Value);
        }

        var request = new HttpRequestFeature
        {
            Method = method,
            Scheme = scheme,
            Path = uri.AbsolutePath,
            PathBase = "",
            QueryString = uri.Query,
            RawTarget = target,
            Protocol = "HTTP/1.1",
            Headers = requestHeaders,
        };
        await using var body = new MemoryStream();
        var response = new CaptureResponseFeature(body);
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
            response.Headers.Location.ToString() is { Length: > 0 } location ? location : null,
            response.Headers.SetCookie.Where(value => value is not null).Select(value => value!).ToArray());
    }
}

internal sealed class CaptureResponseFeature(Stream body) : IHttpResponseFeature
{
    private readonly Stack<(Func<object, Task> Callback, object State)> _starting = new();
    private readonly Stack<(Func<object, Task> Callback, object State)> _completed = new();

    public int StatusCode { get; set; } = StatusCodes.Status200OK;
    public string? ReasonPhrase { get; set; }
    public IHeaderDictionary Headers { get; set; } = new HeaderDictionary();
    public Stream Body { get; set; } = body;
    public bool HasStarted { get; private set; }

    public void OnStarting(Func<object, Task> callback, object state)
        => _starting.Push((callback, state));

    public void OnCompleted(Func<object, Task> callback, object state)
        => _completed.Push((callback, state));

    public async Task FireOnStartingAsync()
    {
        while (_starting.TryPop(out var registration))
            await registration.Callback(registration.State);
        HasStarted = true;
        while (_completed.TryPop(out var registration))
            await registration.Callback(registration.State);
    }
}

internal static class Cookies
{
    public static string RequiredSetCookie(Exchange exchange, string name)
        => exchange.SetCookies.FirstOrDefault(line =>
               line.StartsWith(name + "=", StringComparison.Ordinal))
           ?? throw new InvalidOperationException(
               $"response did not set {name}; headers: [{string.Join(" | ", exchange.SetCookies)}]");

    public static string Pair(string setCookie)
        => setCookie.Split(';', 2)[0];

    public static string Value(string setCookie)
    {
        var pair = Pair(setCookie);
        var equals = pair.IndexOf('=');
        return pair[(equals + 1)..];
    }

    public static IReadOnlyDictionary<string, string> Header(string pair)
        => new Dictionary<string, string> { ["Cookie"] = pair };

    public static string? ForCrossSiteTopLevelGet(string setCookie)
    {
        var sameSite = AttributeValue(setCookie, "SameSite");
        if (string.Equals(sameSite, "Strict", StringComparison.OrdinalIgnoreCase))
            return null;
        if (string.Equals(sameSite, "None", StringComparison.OrdinalIgnoreCase)
            && !HasFlag(setCookie, "Secure"))
            return null;
        return Pair(setCookie);
    }

    public static void AssertHardened(string line, SameSiteMode sameSite)
    {
        Check.True(HasFlag(line, "Secure"), $"cookie is missing Secure: {line}");
        Check.True(HasFlag(line, "HttpOnly"), $"cookie is missing HttpOnly: {line}");
        Check.Equal("/", AttributeValue(line, "Path"), "cookie root path");
        Check.True(string.Equals(sameSite.ToString(), AttributeValue(line, "SameSite"),
                StringComparison.OrdinalIgnoreCase),
            $"cookie SameSite attribute: expected <{sameSite}> in {line}");
        Check.Equal<string?>(null, AttributeValue(line, "Domain"),
            "__Host- cookie must not carry Domain");
    }

    public static bool HasExpiredDate(string line, DateTimeOffset now)
    {
        var value = AttributeValue(line, "Expires");
        return DateTimeOffset.TryParse(value, out var expires) && expires < now;
    }

    private static bool HasFlag(string line, string name)
        => line.Split(';').Skip(1).Any(part =>
            string.Equals(part.Trim(), name, StringComparison.OrdinalIgnoreCase));

    private static string? AttributeValue(string line, string name)
    {
        foreach (var raw in line.Split(';').Skip(1))
        {
            var part = raw.Trim();
            var equals = part.IndexOf('=');
            if (equals > 0 && string.Equals(part[..equals], name,
                    StringComparison.OrdinalIgnoreCase))
                return part[(equals + 1)..];
        }
        return null;
    }
}

internal static class Check
{
    public static void True(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    public static void Equal<T>(T expected, T actual, string label)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            throw new InvalidOperationException(
                $"{label}: expected <{expected}> but got <{actual}>");
    }

    public static void NotEqual<T>(T unexpected, T actual, string label)
    {
        if (EqualityComparer<T>.Default.Equals(unexpected, actual))
            throw new InvalidOperationException(
                $"{label}: value unexpectedly remained <{actual}>");
    }
}
