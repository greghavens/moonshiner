namespace TokenRefreshStorm;

public sealed record AccessToken(string Value, DateTimeOffset ExpiresAtUtc);
