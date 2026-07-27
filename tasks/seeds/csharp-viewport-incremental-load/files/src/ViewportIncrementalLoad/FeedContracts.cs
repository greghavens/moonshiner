namespace ViewportIncrementalLoad;

public sealed record FeedItem(string Id, string Title);

public sealed record PageResult(
    int PageIndex,
    IReadOnlyList<FeedItem> Items,
    bool HasMore);

public interface IPageSource
{
    Task<PageResult> LoadPageAsync(
        string viewKey,
        int pageIndex,
        int pageSize,
        CancellationToken cancellationToken);
}
