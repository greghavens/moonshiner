using ViewportIncrementalLoad;

var tests = new (string Name, Func<Task> Body)[]
{
    ("desktop and mobile windows share every overlapping page", DesktopAndMobileWindowsSharePages),
    ("visible work promotes an existing prefetch", VisibleWorkPromotesPrefetch),
    ("prefetch joins visible work without extending its caller", PrefetchJoinsVisibleWork),
    ("caller cancellation leaves shared transport alive", CallerCancellationIsLocal),
    ("shared failures fan out and retry", SharedFailureFansOutAndRetries),
    ("source-cancelled prefetch retries when visible", SourceCancelledPrefetchRetries),
    ("out-of-order pages preserve ordering and selection", OrderingAndSelectionRemainStable),
    ("view changes cancel and fence stale work", ViewChangeCancelsAndFences),
    ("invalid source pages remain inspectable and can retry", InvalidSourcePageCanRetry),
    ("validation and terminal-page behavior remain intact", ValidationAndTerminalPageRemainIntact),
    ("disposal cancels and fences active work", DisposalCancelsAndFences)
};

var failures = new List<string>();
foreach ((string name, Func<Task> body) in tests)
{
    try
    {
        await body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failures.Add(name);
        Console.Error.WriteLine(
            $"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }
}

if (failures.Count != 0)
{
    Console.Error.WriteLine(
        $"{failures.Count} test(s) failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"All {tests.Length} protected tests passed.");
return 0;

static async Task DesktopAndMobileWindowsSharePages()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "inbox",
        pageSize: 16,
        prefetchPages: 1);

    Task desktop = controller.UpdateViewportAsync(0, 16);
    Task mobile = controller.UpdateViewportAsync(12, 12);
    await source.WaitForTotalCallsAsync(3);

    Check.Equal(1, source.CallCount("inbox", 0),
        "the overlapping visible page was requested more than once");
    Check.Equal(1, source.CallCount("inbox", 1),
        "mobile visible work did not join the desktop prefetch");
    Check.Equal(1, source.CallCount("inbox", 2),
        "the mobile forward prefetch should start exactly once");
    Check.Equal(3, source.TotalCalls,
        "overlapping windows should issue one request per distinct page");

    source.SingleCall("inbox", 1).Succeed(Page(1, 16));
    source.SingleCall("inbox", 0).Succeed(Page(0, 16));
    await Task.WhenAll(desktop, mobile).WaitAsync(TestTimeout.Value);

    source.SingleCall("inbox", 2).Succeed(Page(2, 16));
    await Check.EventuallyAsync(
        () => controller.Items.Count == 48,
        "the speculative page did not publish");
    Check.SequenceEqual(
        Enumerable.Range(0, 48).Select(index => $"item-{index}"),
        controller.Items.Select(item => item.Id),
        "completed pages must publish in page and source order");
}

static async Task VisibleWorkPromotesPrefetch()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "timeline",
        pageSize: 10,
        prefetchPages: 1);

    Task firstWindow = controller.UpdateViewportAsync(0, 10);
    await source.WaitForTotalCallsAsync(2);
    PendingCall visiblePageZero = source.SingleCall("timeline", 0);
    PendingCall prefetchedPageOne = source.SingleCall("timeline", 1);

    visiblePageZero.Succeed(Page(0, 10));
    await firstWindow.WaitAsync(TestTimeout.Value);
    Check.False(prefetchedPageOne.Task.IsCompleted,
        "viewport completion must not wait for speculative work");

    Task secondWindow = controller.UpdateViewportAsync(10, 10);
    await source.WaitForTotalCallsAsync(3);
    Check.Equal(1, source.CallCount("timeline", 1),
        "visible work should join its already-running prefetch");
    Check.Equal(1, source.CallCount("timeline", 2),
        "moving the viewport should keep forward prefetch eager");

    prefetchedPageOne.Succeed(Page(1, 10));
    await secondWindow.WaitAsync(TestTimeout.Value);
    source.SingleCall("timeline", 2).Succeed(Page(2, 10));
}

static async Task PrefetchJoinsVisibleWork()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "reverse-overlap",
        pageSize: 10,
        prefetchPages: 1);

    Task advancedWindow = controller.UpdateViewportAsync(10, 10);
    await source.WaitForTotalCallsAsync(2);
    PendingCall visiblePageOne = source.SingleCall("reverse-overlap", 1);
    PendingCall prefetchedPageTwo = source.SingleCall("reverse-overlap", 2);

    Task behindWindow = controller.UpdateViewportAsync(0, 10);
    await source.WaitForTotalCallsAsync(3);
    Check.Equal(1, source.CallCount("reverse-overlap", 1),
        "a later prefetch should join an already-visible page request");
    Check.False(visiblePageOne.Task.IsCompleted,
        "the shared visible page should still be controlled by the source");

    source.SingleCall("reverse-overlap", 0).Succeed(Page(0, 10));
    await behindWindow.WaitAsync(TestTimeout.Value);
    Check.False(advancedWindow.IsCompleted,
        "the caller that owns visible page one should still be waiting");
    Check.False(prefetchedPageTwo.Task.IsCompleted,
        "neither viewport should wait for speculative page two");

    visiblePageOne.Succeed(Page(1, 10));
    await advancedWindow.WaitAsync(TestTimeout.Value);
    prefetchedPageTwo.Succeed(Page(2, 10));
}

static async Task CallerCancellationIsLocal()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "shared",
        pageSize: 25,
        prefetchPages: 0);
    using var abandonedWait = new CancellationTokenSource();

    Task first = controller.UpdateViewportAsync(0, 12, abandonedWait.Token);
    Task second = controller.UpdateViewportAsync(5, 12);
    await source.WaitForTotalCallsAsync(1);

    Check.Equal(1, source.TotalCalls,
        "same-page callers must share one transport request");
    PendingCall shared = source.SingleCall("shared", 0);

    abandonedWait.Cancel();
    await Check.ThrowsAsync<OperationCanceledException>(
        () => first,
        "the cancelled caller did not leave its wait promptly");
    Check.False(shared.CancellationToken.IsCancellationRequested,
        "caller cancellation leaked into shared transport ownership");
    Check.False(second.IsCompleted,
        "a second participant should still be waiting for the page");

    shared.Succeed(Page(0, 25));
    await second.WaitAsync(TestTimeout.Value);
    Check.Equal(25, controller.Items.Count,
        "the remaining participant did not receive the shared result");
}

static async Task SharedFailureFansOutAndRetries()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "search",
        pageSize: 10,
        prefetchPages: 0);

    Task first = controller.UpdateViewportAsync(0, 6);
    Task second = controller.UpdateViewportAsync(2, 6);
    await source.WaitForTotalCallsAsync(1);
    Check.Equal(1, source.TotalCalls,
        "concurrent failing callers should still share one attempt");

    var expected = new PageLoadException("temporary gateway failure");
    source.SingleCall("search", 0).Fail(expected);
    Exception firstFailure = await Check.CaptureAsync(first);
    Exception secondFailure = await Check.CaptureAsync(second);
    Check.Same(expected, firstFailure,
        "the first participant should observe the source failure");
    Check.Same(expected, secondFailure,
        "the follower should observe the same source failure");
    Check.Same(expected, controller.GetPageError(0),
        "the shared page failure should remain inspectable");

    Task retry = controller.UpdateViewportAsync(0, 6);
    await source.WaitForTotalCallsAsync(2);
    Check.Equal(2, source.CallCount("search", 0),
        "a failed attempt must leave in-flight state before retry");
    Check.Same(expected, controller.GetPageError(0),
        "starting a retry should not erase the last known error");

    source.CallsFor("search", 0)[1].Succeed(Page(0, 10));
    await retry.WaitAsync(TestTimeout.Value);
    Check.True(controller.GetPageError(0) is null,
        "a successful retry should clear the old page error");
}

static async Task SourceCancelledPrefetchRetries()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "headlines",
        pageSize: 10,
        prefetchPages: 1);

    Task firstWindow = controller.UpdateViewportAsync(0, 10);
    await source.WaitForTotalCallsAsync(2);
    var upstreamCancellation = new OperationCanceledException(
        "the upstream abandoned speculative work",
        innerException: null,
        new CancellationToken(canceled: true));
    source.SingleCall("headlines", 1).Fail(upstreamCancellation);
    source.SingleCall("headlines", 0).Succeed(Page(0, 10));
    await firstWindow.WaitAsync(TestTimeout.Value);

    await Check.EventuallyAsync(
        () => ReferenceEquals(
            controller.GetPageError(1),
            upstreamCancellation),
        "a source cancellation should remain inspectable");

    Task visibleRetry = controller.UpdateViewportAsync(10, 10);
    await source.WaitForTotalCallsAsync(4);
    Check.Equal(2, source.CallCount("headlines", 1),
        "a source-cancelled prefetch must retry when it becomes visible");
    Check.Same(upstreamCancellation, controller.GetPageError(1),
        "the source cancellation should remain until retry succeeds");

    source.CallsFor("headlines", 1)[1].Succeed(Page(1, 10));
    await visibleRetry.WaitAsync(TestTimeout.Value);
    Check.True(controller.GetPageError(1) is null,
        "the successful visible retry should clear the cancellation");
    source.SingleCall("headlines", 2).Succeed(Page(2, 10));
}

static async Task OrderingAndSelectionRemainStable()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "library",
        pageSize: 6,
        prefetchPages: 0);
    controller.Select("item-8");

    Task update = controller.UpdateViewportAsync(4, 8);
    await source.WaitForTotalCallsAsync(2);
    PendingCall pageZero = source.SingleCall("library", 0);
    PendingCall pageOne = source.SingleCall("library", 1);
    PageResult secondResult = Page(1, 6);
    FeedItem selectedInstance = secondResult.Items[2];

    pageOne.Succeed(secondResult);
    await Check.EventuallyAsync(
        () => controller.Items.Count == 6,
        "the independently completed later page was not published");
    Check.SequenceEqual(
        Enumerable.Range(6, 6).Select(index => $"item-{index}"),
        controller.Items.Select(item => item.Id),
        "a lone later page should retain source order");
    Check.Equal("item-8", controller.SelectedId,
        "loading a page changed the selection identity");
    Check.Same(selectedInstance, controller.SelectedItem,
        "selection did not bind when its item appeared");

    pageZero.Succeed(Page(0, 6));
    await update.WaitAsync(TestTimeout.Value);
    Check.SequenceEqual(
        Enumerable.Range(0, 12).Select(index => $"item-{index}"),
        controller.Items.Select(item => item.Id),
        "a late earlier page was not inserted before the later page");
    Check.Equal("item-8", controller.SelectedId,
        "rebuilding ordered items changed the selected ID");
    Check.Same(selectedInstance, controller.SelectedItem,
        "selection did not rebind to the canonical published instance");

    await controller.UpdateViewportAsync(0, 12);
    Check.Equal(2, source.TotalCalls,
        "already loaded pages should remain cache hits");
}

static async Task ViewChangeCancelsAndFences()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "old-view",
        pageSize: 10,
        prefetchPages: 0);
    controller.Select("new-view-3");

    Task oldUpdate = controller.UpdateViewportAsync(0, 10);
    await source.WaitForTotalCallsAsync(1);
    PendingCall oldCall = source.SingleCall("old-view", 0);

    controller.ChangeView("new-view");
    Check.True(oldCall.CancellationToken.IsCancellationRequested,
        "changing view did not cancel old transport ownership");
    Check.Equal(0, controller.Items.Count,
        "changing view did not clear accumulated pages");
    Check.Equal("new-view-3", controller.SelectedId,
        "changing view should preserve selection identity");
    Check.True(controller.SelectedItem is null,
        "selection should be unbound until its new-view item appears");

    Task newUpdate = controller.UpdateViewportAsync(0, 10);
    await source.WaitForTotalCallsAsync(2);
    PendingCall newCall = source.SingleCall("new-view", 0);
    PageResult newResult = NamedPage("new-view", 0, 10);
    newCall.Succeed(newResult);
    await newUpdate.WaitAsync(TestTimeout.Value);

    oldCall.Succeed(NamedPage("old-view", 0, 10));
    await oldUpdate.WaitAsync(TestTimeout.Value);
    Check.Equal("new-view", controller.ViewKey,
        "a stale completion changed the active view");
    Check.SequenceEqual(
        newResult.Items.Select(item => item.Id),
        controller.Items.Select(item => item.Id),
        "a cancellation-ignoring stale response was published");
    Check.Equal("new-view-3", controller.SelectedId,
        "a stale completion changed selection identity");
    Check.Same(newResult.Items[3], controller.SelectedItem,
        "selection did not bind to the new view's item instance");
}

static async Task InvalidSourcePageCanRetry()
{
    var source = new ControlledPageSource();
    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "contract",
        pageSize: 8,
        prefetchPages: 0);

    Task invalid = controller.UpdateViewportAsync(0, 8);
    await source.WaitForTotalCallsAsync(1);
    source.SingleCall("contract", 0).Succeed(Page(7, 8));
    InvalidOperationException failure =
        await Check.ThrowsAsync<InvalidOperationException>(
            () => invalid,
            "a mismatched source page index should fail");
    Check.Same(failure, controller.GetPageError(0),
        "the source contract failure should be inspectable");
    Check.Equal(0, controller.Items.Count,
        "an invalid page must not publish items");

    Task retry = controller.UpdateViewportAsync(0, 8);
    await source.WaitForTotalCallsAsync(2);
    Check.Same(failure, controller.GetPageError(0),
        "the contract error should remain while retry is pending");
    source.CallsFor("contract", 0)[1].Succeed(Page(0, 8));
    await retry.WaitAsync(TestTimeout.Value);
    Check.True(controller.GetPageError(0) is null,
        "a valid retry should clear the contract error");
}

static async Task ValidationAndTerminalPageRemainIntact()
{
    var source = new ControlledPageSource();
    Check.Throws<ArgumentOutOfRangeException>(
        () => new ViewportFeedController(source, "bad-size", 0),
        "zero page size should be rejected");
    Check.Throws<ArgumentOutOfRangeException>(
        () => new ViewportFeedController(source, "bad-prefetch", 5, -1),
        "negative prefetch distance should be rejected");

    using var controller = new ViewportFeedController(
        source,
        initialViewKey: "finite",
        pageSize: 5,
        prefetchPages: 0);
    await Check.ThrowsAsync<ArgumentOutOfRangeException>(
        () => controller.UpdateViewportAsync(-1, 1),
        "negative first index should be rejected");
    await Check.ThrowsAsync<ArgumentOutOfRangeException>(
        () => controller.UpdateViewportAsync(0, -1),
        "negative visible count should be rejected");
    await Check.ThrowsAsync<OverflowException>(
        () => controller.UpdateViewportAsync(int.MaxValue, 2),
        "visible range overflow should be rejected");
    Check.Throws<ArgumentOutOfRangeException>(
        () => controller.GetPageError(-1),
        "negative page errors should be rejected");
    await controller.UpdateViewportAsync(0, 0);
    Check.Equal(0, source.TotalCalls,
        "an empty viewport should not request a page");

    Task lastPage = controller.UpdateViewportAsync(0, 5);
    await source.WaitForTotalCallsAsync(1);
    PendingCall terminal = source.SingleCall("finite", 0);
    controller.ChangeView("finite");
    Check.False(terminal.CancellationToken.IsCancellationRequested,
        "changing to the current view should be a no-op");
    terminal.Succeed(Page(0, 5, hasMore: false));
    await lastPage.WaitAsync(TestTimeout.Value);
    await controller.UpdateViewportAsync(5, 5);

    Check.Equal(1, source.TotalCalls,
        "a known terminal page should suppress later page requests");
    Check.True(controller.GetPageError(1) is null,
        "skipping a beyond-terminal page should not create an error");
}

static async Task DisposalCancelsAndFences()
{
    var source = new ControlledPageSource();
    var controller = new ViewportFeedController(
        source,
        initialViewKey: "disposable",
        pageSize: 4,
        prefetchPages: 0);

    Task update = controller.UpdateViewportAsync(0, 4);
    await source.WaitForTotalCallsAsync(1);
    PendingCall active = source.SingleCall("disposable", 0);
    controller.Dispose();
    controller.Dispose();

    Check.True(active.CancellationToken.IsCancellationRequested,
        "disposing did not cancel active transport ownership");
    active.Succeed(Page(0, 4));
    await update.WaitAsync(TestTimeout.Value);
    Check.Equal(0, controller.Items.Count,
        "a cancellation-ignoring completion published after disposal");
    await Check.ThrowsAsync<ObjectDisposedException>(
        () => controller.UpdateViewportAsync(0, 4),
        "viewport updates should reject use after disposal");
}

static PageResult Page(int pageIndex, int pageSize, bool hasMore = true) =>
    new(
        pageIndex,
        Enumerable.Range(pageIndex * pageSize, pageSize)
            .Select(index => new FeedItem($"item-{index}", $"Item {index}"))
            .ToArray(),
        hasMore);

static PageResult NamedPage(
    string prefix,
    int pageIndex,
    int pageSize,
    bool hasMore = true) =>
    new(
        pageIndex,
        Enumerable.Range(pageIndex * pageSize, pageSize)
            .Select(index => new FeedItem(
                $"{prefix}-{index}",
                $"{prefix} item {index}"))
            .ToArray(),
        hasMore);

internal static class TestTimeout
{
    public static readonly TimeSpan Value = TimeSpan.FromSeconds(4);
}

internal sealed class ControlledPageSource : IPageSource
{
    private readonly object _gate = new();
    private readonly List<PendingCall> _calls = [];
    private TaskCompletionSource _changed = NewSignal();

    public int TotalCalls
    {
        get
        {
            lock (_gate)
            {
                return _calls.Count;
            }
        }
    }

    public Task<PageResult> LoadPageAsync(
        string viewKey,
        int pageIndex,
        int pageSize,
        CancellationToken cancellationToken)
    {
        TaskCompletionSource signal;
        var call = new PendingCall(
            viewKey,
            pageIndex,
            pageSize,
            cancellationToken);

        lock (_gate)
        {
            _calls.Add(call);
            signal = _changed;
            _changed = NewSignal();
        }

        signal.TrySetResult();
        return call.Task;
    }

    public int CallCount(string viewKey, int pageIndex) =>
        CallsFor(viewKey, pageIndex).Count;

    public IReadOnlyList<PendingCall> CallsFor(
        string viewKey,
        int pageIndex)
    {
        lock (_gate)
        {
            return _calls
                .Where(call =>
                    StringComparer.Ordinal.Equals(call.ViewKey, viewKey) &&
                    call.PageIndex == pageIndex)
                .ToArray();
        }
    }

    public PendingCall SingleCall(string viewKey, int pageIndex)
    {
        IReadOnlyList<PendingCall> matches = CallsFor(viewKey, pageIndex);
        Check.Equal(1, matches.Count,
            $"expected one call for {viewKey} page {pageIndex}");
        return matches[0];
    }

    public async Task WaitForTotalCallsAsync(int expected)
    {
        using var timeout = new CancellationTokenSource(TestTimeout.Value);
        while (true)
        {
            Task changed;
            lock (_gate)
            {
                if (_calls.Count >= expected)
                {
                    return;
                }

                changed = _changed.Task;
            }

            await changed.WaitAsync(timeout.Token);
        }
    }

    private static TaskCompletionSource NewSignal() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);
}

internal sealed class PendingCall
{
    private readonly TaskCompletionSource<PageResult> _completion =
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    public PendingCall(
        string viewKey,
        int pageIndex,
        int pageSize,
        CancellationToken cancellationToken)
    {
        ViewKey = viewKey;
        PageIndex = pageIndex;
        PageSize = pageSize;
        CancellationToken = cancellationToken;
    }

    public string ViewKey { get; }

    public int PageIndex { get; }

    public int PageSize { get; }

    public CancellationToken CancellationToken { get; }

    public Task<PageResult> Task => _completion.Task;

    public void Succeed(PageResult page) => _completion.TrySetResult(page);

    public void Fail(Exception exception) =>
        _completion.TrySetException(exception);
}

internal static class Check
{
    public static void Equal<T>(T expected, T actual, string message)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new TestFailureException(
                $"{message}: expected '{expected}', got '{actual}'");
        }
    }

    public static void Same(object? expected, object? actual, string message)
    {
        if (!ReferenceEquals(expected, actual))
        {
            throw new TestFailureException(message);
        }
    }

    public static void True(bool condition, string message)
    {
        if (!condition)
        {
            throw new TestFailureException(message);
        }
    }

    public static void False(bool condition, string message) =>
        True(!condition, message);

    public static void SequenceEqual<T>(
        IEnumerable<T> expected,
        IEnumerable<T> actual,
        string message)
    {
        T[] expectedValues = expected.ToArray();
        T[] actualValues = actual.ToArray();
        if (!expectedValues.SequenceEqual(actualValues))
        {
            throw new TestFailureException(
                $"{message}: expected [{string.Join(", ", expectedValues)}], " +
                $"got [{string.Join(", ", actualValues)}]");
        }
    }

    public static void Throws<TException>(Action action, string message)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException)
        {
            return;
        }
        catch (Exception exception)
        {
            throw new TestFailureException(
                $"{message}: expected {typeof(TException).Name}, " +
                $"got {exception.GetType().Name}");
        }

        throw new TestFailureException(
            $"{message}: expected {typeof(TException).Name}, got no exception");
    }

    public static async Task<TException> ThrowsAsync<TException>(
        Func<Task> action,
        string message)
        where TException : Exception
    {
        Exception exception = await CaptureAsync(action());
        if (exception is not TException expected)
        {
            throw new TestFailureException(
                $"{message}: expected {typeof(TException).Name}, " +
                $"got {exception.GetType().Name}");
        }

        return expected;
    }

    public static async Task<Exception> CaptureAsync(Task task)
    {
        try
        {
            await task.WaitAsync(TestTimeout.Value);
        }
        catch (Exception exception)
        {
            return exception;
        }

        throw new TestFailureException("expected the operation to fail");
    }

    public static async Task EventuallyAsync(
        Func<bool> predicate,
        string message)
    {
        using var timeout = new CancellationTokenSource(TestTimeout.Value);
        while (!predicate())
        {
            try
            {
                await Task.Delay(1, timeout.Token);
            }
            catch (OperationCanceledException)
            {
                throw new TestFailureException(message);
            }
        }
    }
}

internal sealed class PageLoadException(string message) : Exception(message);

internal sealed class TestFailureException(string message) : Exception(message);
