namespace TokenRefreshStorm;

public interface IClock
{
    DateTimeOffset UtcNow { get; }
}
