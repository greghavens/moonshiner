namespace RoastQueue;

public class RoastProfile
{
    public required string Bean { get; init; }
    public required int TargetTemp { get; init; }
}

/// <summary>
/// Drives the shop's single drum roaster through the day's order queue.
/// The preheater and the profile lookup are injected so the panel software
/// and the test rig can both drive it.
/// </summary>
public class Roaster
{
    private readonly Func<Task> _preheat;
    private readonly Func<string, Task<RoastProfile>> _profiles;
    private readonly Queue<string> _pending = new();
    private readonly List<string> _published = new();

    public Roaster(Func<Task> preheat, Func<string, Task<RoastProfile>> profiles)
    {
        _preheat = preheat;
        _profiles = profiles;
    }

    public string Phase { get; private set; } = "idle";

    public IReadOnlyList<string> Published => _published;

    public void Enqueue(string order)
    {
        _pending.Enqueue(order);
    }

    public Task<int> DepthAsync()
    {
        return _pending.Count;
    }

    /// <summary>Preheats the drum, then moves the next order into the roasting phase.</summary>
    public async void RunNextAsync()
    {
        if (_pending.Count == 0)
        {
            throw new InvalidOperationException("nothing queued");
        }
        Phase = "preheating";
        await _preheat();
        Phase = "roasting:" + _pending.Dequeue();
    }

    /// <summary>Runs the whole queue down, one order at a time.</summary>
    public async Task RunAllAsync()
    {
        while (_pending.Count > 0)
        {
            await RunNextAsync();
        }
        Phase = "idle";
    }

    /// <summary>Posts the roast card for an order to the shop display.</summary>
    public async Task AnnounceAsync(string order)
    {
        Publish(_profiles(order));
    }

    /// <summary>Bag label for the next queued order, e.g. "ORD-9: guji natural".</summary>
    public Task<string> NextLabelAsync()
    {
        if (_pending.Count == 0)
        {
            throw new InvalidOperationException("nothing queued");
        }
        var order = _pending.Dequeue();
        var profile = _profiles(order).Result;
        return Task.FromResult(order + ": " + profile.Bean);
    }

    /// <summary>Parks the drum at the end of the day.</summary>
    public async Task ShutDownAsync()
    {
        Phase = "cooling";
        Phase = "idle";
        return Task.CompletedTask;
    }

    private void Publish(RoastProfile profile)
    {
        _published.Add(profile.Bean + " @ " + profile.TargetTemp + "C");
    }
}
