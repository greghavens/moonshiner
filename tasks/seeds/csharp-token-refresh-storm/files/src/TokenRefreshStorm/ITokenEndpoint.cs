namespace TokenRefreshStorm;

public interface ITokenEndpoint
{
    Task<AccessToken> RefreshAsync(CancellationToken cancellationToken);
}
