namespace RoastQueue;

public class RoasterTests
{
    private static RoastProfile Guji => new() { Bean = "guji natural", TargetTemp = 208 };

    private static Task<RoastProfile> InstantProfile(string order) => Task.FromResult(Guji);

    [Fact]
    public async Task Depth_counts_queued_orders()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        r.Enqueue("ORD-1");
        r.Enqueue("ORD-2");
        Assert.Equal(2, await r.DepthAsync());
    }

    [Fact]
    public async Task RunNext_preheats_then_roasts_the_next_order()
    {
        var preheat = new TaskCompletionSource();
        var r = new Roaster(() => preheat.Task, InstantProfile);
        r.Enqueue("ORD-1");

        Task run = r.RunNextAsync();
        Assert.Equal("preheating", r.Phase);
        Assert.False(run.IsCompleted);

        preheat.SetResult();
        await run;
        Assert.Equal("roasting:ORD-1", r.Phase);
        Assert.Equal(0, await r.DepthAsync());
    }

    [Fact]
    public async Task RunNext_with_an_empty_queue_is_an_error()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        await Assert.ThrowsAsync<InvalidOperationException>(() => r.RunNextAsync());
    }

    [Fact]
    public async Task RunAll_drains_the_queue_and_parks_idle()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        r.Enqueue("ORD-1");
        r.Enqueue("ORD-2");
        await r.RunAllAsync();
        Assert.Equal("idle", r.Phase);
        Assert.Equal(0, await r.DepthAsync());
    }

    [Fact]
    public async Task Announce_posts_the_roast_card()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        await r.AnnounceAsync("ORD-1");
        Assert.Equal(new[] { "guji natural @ 208C" }, r.Published);
    }

    [Fact]
    public async Task Announce_waits_for_the_profile_lookup()
    {
        var lookup = new TaskCompletionSource<RoastProfile>();
        var r = new Roaster(() => Task.CompletedTask, _ => lookup.Task);
        var announce = r.AnnounceAsync("ORD-1");
        Assert.False(announce.IsCompleted);
        Assert.Empty(r.Published);

        lookup.SetResult(Guji);
        await announce;
        Assert.Equal(new[] { "guji natural @ 208C" }, r.Published);
    }

    [Fact]
    public async Task NextLabel_does_not_block_while_the_profile_is_in_flight()
    {
        var lookup = new TaskCompletionSource<RoastProfile>(TaskCreationOptions.RunContinuationsAsynchronously);
        var r = new Roaster(() => Task.CompletedTask, _ => lookup.Task);
        r.Enqueue("ORD-9");

        Task<string>? label = null;
        var call = Task.Run(() => { label = r.NextLabelAsync(); });
        var winner = await Task.WhenAny(call, Task.Delay(TimeSpan.FromSeconds(5)));
        bool returnedPromptly = ReferenceEquals(call, winner);
        bool labelStillPending = returnedPromptly && !label!.IsCompleted;

        lookup.SetResult(Guji);
        Assert.True(returnedPromptly,
            "NextLabelAsync should hand back a task immediately instead of blocking on the profile lookup");
        Assert.True(labelStillPending,
            "the label task should still be pending until the profile arrives");
        Assert.Equal("ORD-9: guji natural", await label!);
    }

    [Fact]
    public async Task NextLabel_with_an_empty_queue_is_an_error()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        await Assert.ThrowsAsync<InvalidOperationException>(() => r.NextLabelAsync());
    }

    [Fact]
    public async Task ShutDown_parks_the_drum_idle()
    {
        var r = new Roaster(() => Task.CompletedTask, InstantProfile);
        r.Enqueue("ORD-1");
        await r.RunAllAsync();
        await r.ShutDownAsync();
        Assert.Equal("idle", r.Phase);
    }
}
