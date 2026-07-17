using System.Threading.Channels;

namespace BagLine;

public class CarouselBasicsTests
{
    [Fact]
    public void CapacityMustBePositive()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new MakeupCarousel(0));
        Assert.Throws<ArgumentOutOfRangeException>(() => new MakeupCarousel(-3));
    }

    [Fact]
    public async Task BagsComeOffInPlacementOrder()
    {
        var carousel = new MakeupCarousel(8);
        var tags = new[] { "BA1201", "BA1202", "BA1203", "BA1204", "BA1205" };
        foreach (var tag in tags)
            await carousel.PlaceAsync(new Bag(tag, "AK404"));
        carousel.CloseFlight();

        var loaded = await carousel.LoadAsync(bag => Task.FromResult($"{bag.Tag}->cart1"));

        Assert.Equal(
            new[] { "BA1201->cart1", "BA1202->cart1", "BA1203->cart1", "BA1204->cart1", "BA1205->cart1" },
            loaded);
    }

    [Fact]
    public async Task EmptyClosedCarouselDrainsToNothing()
    {
        var carousel = new MakeupCarousel(4);
        carousel.CloseFlight();

        var loaded = await carousel.LoadAsync(bag => Task.FromResult(bag.Tag));

        Assert.Empty(loaded);
    }

    [Fact]
    public async Task TryPlaceReportsHonestly()
    {
        var carousel = new MakeupCarousel(2);
        Assert.True(carousel.TryPlace(new Bag("BA1201", "AK404")));
        Assert.True(carousel.TryPlace(new Bag("BA1202", "AK404")));
        Assert.False(carousel.TryPlace(new Bag("BA1203", "AK404")));

        carousel.CloseFlight();
        var loaded = await carousel.LoadAsync(bag => Task.FromResult(bag.Tag));
        Assert.Equal(new[] { "BA1201", "BA1202" }, loaded);
    }
}

public class BackpressureTests
{
    [Fact]
    public async Task PlacingBeyondCapacityWaitsForASlot()
    {
        var carousel = new MakeupCarousel(2);
        var place1 = carousel.PlaceAsync(new Bag("BA1201", "AK404"));
        var place2 = carousel.PlaceAsync(new Bag("BA1202", "AK404"));
        Assert.True(place1.IsCompleted);
        Assert.True(place2.IsCompleted);

        var pending = carousel.PlaceAsync(new Bag("BA1203", "AK404"));
        Assert.False(pending.IsCompleted);   // belt is full — the wait is real

        // scripted loader: each bag announces itself, then waits for its gate
        var started = new Dictionary<string, TaskCompletionSource>
        {
            ["BA1201"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1202"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1203"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
        };
        var gates = new Dictionary<string, TaskCompletionSource>
        {
            ["BA1201"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1202"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1203"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
        };
        var drain = carousel.LoadAsync(async bag =>
        {
            started[bag.Tag].SetResult();
            await gates[bag.Tag].Task;
            return $"{bag.Tag}->cart2";
        });

        await started["BA1201"].Task;   // consumer took BA1201, freeing one slot
        await pending;                  // so the blocked placement lands

        Assert.False(carousel.TryPlace(new Bag("BA1209", "AK404")));   // full again

        gates["BA1201"].SetResult();
        gates["BA1202"].SetResult();
        gates["BA1203"].SetResult();
        carousel.CloseFlight();

        Assert.Equal(new[] { "BA1201->cart2", "BA1202->cart2", "BA1203->cart2" }, await drain);
    }

    [Fact]
    public async Task CanceledWaitNeverAddsItsBag()
    {
        var carousel = new MakeupCarousel(1);
        await carousel.PlaceAsync(new Bag("BA1201", "AK404"));

        using var cts = new CancellationTokenSource();
        var pending = carousel.PlaceAsync(new Bag("BA1202", "AK404"), cts.Token);
        Assert.False(pending.IsCompleted);

        cts.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => pending);

        carousel.CloseFlight();
        var loaded = await carousel.LoadAsync(bag => Task.FromResult(bag.Tag));
        Assert.Equal(new[] { "BA1201" }, loaded);
    }
}

public class DrainDisciplineTests
{
    [Fact]
    public async Task LoaderHandlesOneBagAtATime()
    {
        var carousel = new MakeupCarousel(4);
        await carousel.PlaceAsync(new Bag("BA1201", "AK404"));
        await carousel.PlaceAsync(new Bag("BA1202", "AK404"));
        carousel.CloseFlight();

        var starts = new List<string>();
        var started = new Dictionary<string, TaskCompletionSource>
        {
            ["BA1201"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1202"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
        };
        var gates = new Dictionary<string, TaskCompletionSource>
        {
            ["BA1201"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
            ["BA1202"] = new(TaskCreationOptions.RunContinuationsAsynchronously),
        };
        var drain = carousel.LoadAsync(async bag =>
        {
            starts.Add(bag.Tag);
            started[bag.Tag].SetResult();
            await gates[bag.Tag].Task;
            return bag.Tag;
        });

        await started["BA1201"].Task;
        Assert.Equal(new[] { "BA1201" }, starts);   // second bag must NOT be in flight yet

        gates["BA1201"].SetResult();
        await started["BA1202"].Task;
        Assert.Equal(new[] { "BA1201", "BA1202" }, starts);

        gates["BA1202"].SetResult();
        Assert.Equal(new[] { "BA1201", "BA1202" }, await drain);
    }

    [Fact]
    public async Task ClosedFlightRefusesNewBagsAndCloseIsIdempotent()
    {
        var carousel = new MakeupCarousel(2);
        await carousel.PlaceAsync(new Bag("BA1201", "AK404"));
        carousel.CloseFlight();
        carousel.CloseFlight();   // closing twice is fine

        Assert.False(carousel.TryPlace(new Bag("BA1202", "AK404")));
        await Assert.ThrowsAsync<ChannelClosedException>(
            () => carousel.PlaceAsync(new Bag("BA1203", "AK404")));

        var loaded = await carousel.LoadAsync(bag => Task.FromResult(bag.Tag));
        Assert.Equal(new[] { "BA1201" }, loaded);
    }
}
