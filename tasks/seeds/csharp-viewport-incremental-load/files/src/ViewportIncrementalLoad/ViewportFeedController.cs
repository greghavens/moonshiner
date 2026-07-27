namespace ViewportIncrementalLoad;

public sealed class ViewportFeedController : IDisposable
{
    private readonly object _gate = new();
    private readonly IPageSource _source;
    private readonly int _pageSize;
    private readonly int _prefetchPages;
    private readonly SortedDictionary<int, PageResult> _pages = new();
    private readonly Dictionary<int, Exception> _pageErrors = new();

    private CancellationTokenSource _viewCancellation = new();
    private string _viewKey;
    private long _generation;
    private int? _lastPageIndex;
    private FeedItem[] _items = [];
    private string? _selectedId;
    private FeedItem? _selectedItem;
    private bool _disposed;

    public ViewportFeedController(
        IPageSource source,
        string initialViewKey,
        int pageSize,
        int prefetchPages = 1)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentException.ThrowIfNullOrWhiteSpace(initialViewKey);

        if (pageSize <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(pageSize));
        }

        if (prefetchPages < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(prefetchPages));
        }

        _source = source;
        _viewKey = initialViewKey;
        _pageSize = pageSize;
        _prefetchPages = prefetchPages;
    }

    public string ViewKey
    {
        get
        {
            lock (_gate)
            {
                return _viewKey;
            }
        }
    }

    public IReadOnlyList<FeedItem> Items
    {
        get
        {
            lock (_gate)
            {
                return _items;
            }
        }
    }

    public string? SelectedId
    {
        get
        {
            lock (_gate)
            {
                return _selectedId;
            }
        }
    }

    public FeedItem? SelectedItem
    {
        get
        {
            lock (_gate)
            {
                return _selectedItem;
            }
        }
    }

    public Exception? GetPageError(int pageIndex)
    {
        if (pageIndex < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(pageIndex));
        }

        lock (_gate)
        {
            return _pageErrors.GetValueOrDefault(pageIndex);
        }
    }

    public void Select(string? itemId)
    {
        lock (_gate)
        {
            ThrowIfDisposed();
            _selectedId = itemId;
            RebindSelectionLocked();
        }
    }

    public void ChangeView(string viewKey)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(viewKey);

        CancellationTokenSource previous;
        lock (_gate)
        {
            ThrowIfDisposed();
            if (StringComparer.Ordinal.Equals(_viewKey, viewKey))
            {
                return;
            }

            previous = _viewCancellation;
            _viewCancellation = new CancellationTokenSource();
            _viewKey = viewKey;
            _generation++;
            _pages.Clear();
            _pageErrors.Clear();
            _lastPageIndex = null;
            _items = [];
            RebindSelectionLocked();
        }

        previous.Cancel();
        previous.Dispose();
    }

    public async Task UpdateViewportAsync(
        int firstVisibleIndex,
        int visibleCount,
        CancellationToken cancellationToken = default)
    {
        if (firstVisibleIndex < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(firstVisibleIndex));
        }

        if (visibleCount < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(visibleCount));
        }

        cancellationToken.ThrowIfCancellationRequested();

        if (visibleCount == 0)
        {
            lock (_gate)
            {
                ThrowIfDisposed();
            }

            return;
        }

        int firstPage = firstVisibleIndex / _pageSize;
        int lastVisibleIndex = checked(firstVisibleIndex + visibleCount - 1);
        int lastVisiblePage = lastVisibleIndex / _pageSize;

        var visibleLoads = new List<Task>(lastVisiblePage - firstPage + 1);
        for (int pageIndex = firstPage; pageIndex <= lastVisiblePage; pageIndex++)
        {
            visibleLoads.Add(EnsurePageAsync(pageIndex));
        }

        for (int offset = 1; offset <= _prefetchPages; offset++)
        {
            int pageIndex = checked(lastVisiblePage + offset);
            _ = ObservePrefetchAsync(EnsurePageAsync(pageIndex));
        }

        await Task.WhenAll(visibleLoads)
            .WaitAsync(cancellationToken)
            .ConfigureAwait(false);
    }

    public void Dispose()
    {
        CancellationTokenSource? cancellation = null;
        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            cancellation = _viewCancellation;
        }

        cancellation.Cancel();
        cancellation.Dispose();
    }

    private Task EnsurePageAsync(int pageIndex)
    {
        string viewKey;
        long generation;
        CancellationToken viewToken;

        lock (_gate)
        {
            ThrowIfDisposed();

            if (_pages.ContainsKey(pageIndex) ||
                (_lastPageIndex is int lastPage && pageIndex > lastPage))
            {
                return Task.CompletedTask;
            }

            _pageErrors.Remove(pageIndex);
            viewKey = _viewKey;
            generation = _generation;
            viewToken = _viewCancellation.Token;
        }

        return LoadAndPublishAsync(
            viewKey,
            generation,
            pageIndex,
            viewToken);
    }

    private async Task LoadAndPublishAsync(
        string viewKey,
        long generation,
        int pageIndex,
        CancellationToken viewToken)
    {
        try
        {
            PageResult result = await _source.LoadPageAsync(
                    viewKey,
                    pageIndex,
                    _pageSize,
                    viewToken)
                .ConfigureAwait(false);

            if (result.PageIndex != pageIndex)
            {
                throw new InvalidOperationException(
                    $"The source returned page {result.PageIndex} for request {pageIndex}.");
            }

            lock (_gate)
            {
                if (_disposed || generation != _generation)
                {
                    return;
                }

                _pages[pageIndex] = result;
                _pageErrors.Remove(pageIndex);
                if (!result.HasMore)
                {
                    _lastPageIndex = _lastPageIndex is int knownLast
                        ? Math.Min(knownLast, pageIndex)
                        : pageIndex;
                }

                RebuildItemsLocked();
            }
        }
        catch (Exception exception)
        {
            lock (_gate)
            {
                if (!_disposed && generation == _generation)
                {
                    _pageErrors[pageIndex] = exception;
                }
            }

            throw;
        }
    }

    private static async Task ObservePrefetchAsync(Task prefetch)
    {
        try
        {
            await prefetch.ConfigureAwait(false);
        }
        catch
        {
            // The error is retained by the controller and retried when requested.
        }
    }

    private void RebuildItemsLocked()
    {
        _items = _pages
            .OrderBy(pair => pair.Key)
            .SelectMany(pair => pair.Value.Items)
            .ToArray();
        RebindSelectionLocked();
    }

    private void RebindSelectionLocked()
    {
        _selectedItem = _selectedId is null
            ? null
            : _items.FirstOrDefault(
                item => StringComparer.Ordinal.Equals(item.Id, _selectedId));
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
    }
}
