using System.Collections.Concurrent;

namespace StampedeCache;

public sealed class AsyncExpiringCache<TKey, TValue>
    where TKey : notnull
{
    private readonly ConcurrentDictionary<TKey, CacheEntry> _entries;
    private readonly ICacheClock _clock;

    public AsyncExpiringCache(
        ICacheClock? clock = null,
        IEqualityComparer<TKey>? comparer = null)
    {
        _clock = clock ?? SystemCacheClock.Instance;
        _entries = new ConcurrentDictionary<TKey, CacheEntry>(
            comparer ?? EqualityComparer<TKey>.Default);
    }

    public async Task<TValue> GetOrCreateAsync(
        TKey key,
        Func<CancellationToken, Task<TValue>> valueFactory,
        TimeSpan timeToLive,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(key);
        ArgumentNullException.ThrowIfNull(valueFactory);

        if (timeToLive <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(
                nameof(timeToLive),
                "The cache lifetime must be positive.");
        }

        if (TryGetFresh(key, out var cached))
        {
            return cached;
        }

        var value = await valueFactory(cancellationToken).ConfigureAwait(false);
        _entries[key] = new CacheEntry(value, _clock.UtcNow.Add(timeToLive));
        return value;
    }

    private bool TryGetFresh(TKey key, out TValue value)
    {
        if (_entries.TryGetValue(key, out var entry) &&
            entry.ExpiresAt > _clock.UtcNow)
        {
            value = entry.Value;
            return true;
        }

        value = default!;
        return false;
    }

    private sealed record CacheEntry(TValue Value, DateTimeOffset ExpiresAt);
}
