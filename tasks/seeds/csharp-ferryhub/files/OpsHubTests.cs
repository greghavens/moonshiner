namespace FerryHub;

public class DispatchOrderTests
{
    [Fact]
    public void HandlersRunInSubscriptionOrder()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        hub.Subscribe<BerthAssigned>(e => log.Add($"board:{e.Sailing}->{e.Berth}"));
        hub.Subscribe<BerthAssigned>(e => log.Add($"pager:{e.Sailing}->{e.Berth}"));
        hub.Subscribe<BerthAssigned>(e => log.Add($"gates:{e.Sailing}->{e.Berth}"));

        var result = hub.Publish(new BerthAssigned("MV-Kestrel-0740", 3));

        Assert.Equal(new[] { "board:MV-Kestrel-0740->3", "pager:MV-Kestrel-0740->3", "gates:MV-Kestrel-0740->3" }, log);
        Assert.Equal(3, result.Delivered);
        Assert.Empty(result.Errors);
    }

    [Fact]
    public void DistinctEventTypesDoNotCrossDeliver()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        hub.Subscribe<BerthAssigned>(e => log.Add($"berth:{e.Berth}"));
        hub.Subscribe<RampFault>(e => log.Add($"ramp:{e.Ramp}"));

        var result = hub.Publish(new RampFault(2, "hydraulic pressure low"));

        Assert.Equal(new[] { "ramp:2" }, log);
        Assert.Equal(1, result.Delivered);
    }

    [Fact]
    public void PublishWithNoSubscribersIsQuiet()
    {
        var hub = new OpsHub();

        var result = hub.Publish(new SailingDelayed("MV-Kestrel-0740", 25));

        Assert.Equal(0, result.Delivered);
        Assert.Empty(result.Errors);
    }

    [Fact]
    public void SameHandlerSubscribedTwiceRunsTwiceAndTokensAreIndependent()
    {
        var hub = new OpsHub();
        var count = 0;
        Action<RampFault> handler = _ => count++;
        var first = hub.Subscribe(handler);
        var second = hub.Subscribe(handler);

        hub.Publish(new RampFault(1, "sensor glitch"));
        Assert.Equal(2, count);

        Assert.True(hub.Unsubscribe(first));
        hub.Publish(new RampFault(1, "sensor glitch"));
        Assert.Equal(3, count);

        Assert.True(hub.Unsubscribe(second));
        Assert.False(hub.Unsubscribe(second));
    }

    [Fact]
    public void NullHandlerIsRejectedUpFront()
    {
        var hub = new OpsHub();
        Assert.Throws<ArgumentNullException>(() => hub.Subscribe<BerthAssigned>(null!));
    }
}

public class ExceptionIsolationTests
{
    [Fact]
    public void ThrowingHandlerDoesNotStopTheRest()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        hub.Subscribe<SailingDelayed>(e => log.Add("board"));
        hub.Subscribe<SailingDelayed>(e => { log.Add("pager"); throw new InvalidOperationException("pager offline"); });
        hub.Subscribe<SailingDelayed>(e => log.Add("kiosk"));
        hub.Subscribe<SailingDelayed>(e => log.Add("radio"));

        var result = hub.Publish(new SailingDelayed("MV-Osprey-1215", 40));

        Assert.Equal(new[] { "board", "pager", "kiosk", "radio" }, log);
        Assert.Equal(3, result.Delivered);
        var error = Assert.Single(result.Errors);
        Assert.IsType<InvalidOperationException>(error);
        Assert.Equal("pager offline", error.Message);
    }

    [Fact]
    public void ErrorsComeBackInHandlerOrder()
    {
        var hub = new OpsHub();
        hub.Subscribe<RampFault>(_ => throw new InvalidOperationException("first"));
        hub.Subscribe<RampFault>(_ => { });
        hub.Subscribe<RampFault>(_ => throw new InvalidOperationException("second"));

        var result = hub.Publish(new RampFault(4, "gate jam"));

        Assert.Equal(1, result.Delivered);
        Assert.Equal(new[] { "first", "second" }, result.Errors.Select(e => e.Message).ToArray());
    }
}

public class SnapshotSemanticsTests
{
    [Fact]
    public void SelfUnsubscribeTakesEffectNextPublish()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        Subscription? token = null;
        token = hub.Subscribe<BerthAssigned>(e =>
        {
            log.Add("once");
            hub.Unsubscribe(token!);
        });
        hub.Subscribe<BerthAssigned>(e => log.Add("always"));

        hub.Publish(new BerthAssigned("MV-Kestrel-0740", 3));
        hub.Publish(new BerthAssigned("MV-Kestrel-0740", 5));

        Assert.Equal(new[] { "once", "always", "always" }, log);
    }

    [Fact]
    public void UnsubscribingALaterHandlerMidDispatchStillRunsItThisTime()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        Subscription? doomed = null;
        hub.Subscribe<RampFault>(e =>
        {
            log.Add("chief");
            hub.Unsubscribe(doomed!);
        });
        doomed = hub.Subscribe<RampFault>(e => log.Add("trainee"));

        hub.Publish(new RampFault(1, "loose plate"));
        hub.Publish(new RampFault(1, "loose plate"));

        Assert.Equal(new[] { "chief", "trainee", "chief" }, log);
    }

    [Fact]
    public void SubscribingDuringDispatchTakesEffectNextPublish()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        var hooked = false;
        hub.Subscribe<SailingDelayed>(e =>
        {
            log.Add("dispatch");
            if (!hooked)
            {
                hooked = true;
                hub.Subscribe<SailingDelayed>(_ => log.Add("standby-crew"));
            }
        });

        hub.Publish(new SailingDelayed("MV-Osprey-1215", 10));
        hub.Publish(new SailingDelayed("MV-Osprey-1215", 20));

        Assert.Equal(new[] { "dispatch", "dispatch", "standby-crew" }, log);
    }
}

public class DispatchedTapTests
{
    [Fact]
    public void TapDelegatesFireInAddOrderAndMinusRemoves()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        Action<string, int> tap1 = (name, delivered) => log.Add($"tap1:{name}:{delivered}");
        Action<string, int> tap2 = (name, delivered) => log.Add($"tap2:{name}:{delivered}");
        hub.Dispatched += tap1;
        hub.Dispatched += tap2;
        hub.Subscribe<BerthAssigned>(_ => { });

        hub.Publish(new BerthAssigned("MV-Kestrel-0740", 3));
        Assert.Equal(new[] { "tap1:BerthAssigned:1", "tap2:BerthAssigned:1" }, log);

        log.Clear();
        hub.Dispatched -= tap1;
        hub.Publish(new BerthAssigned("MV-Kestrel-0740", 4));
        Assert.Equal(new[] { "tap2:BerthAssigned:1" }, log);
    }

    [Fact]
    public void TapFiresAfterHandlersAndDespiteHandlerErrors()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        hub.Subscribe<RampFault>(_ => { log.Add("throws"); throw new InvalidOperationException("boom"); });
        hub.Subscribe<RampFault>(_ => log.Add("logs"));
        hub.Dispatched += (name, delivered) => log.Add($"tap:{name}:{delivered}");

        var result = hub.Publish(new RampFault(6, "belt misaligned"));

        Assert.Equal(new[] { "throws", "logs", "tap:RampFault:1" }, log);
        Assert.Equal(1, result.Delivered);
        Assert.Single(result.Errors);
    }

    [Fact]
    public void TapFiresEvenWhenNobodyIsSubscribed()
    {
        var hub = new OpsHub();
        var log = new List<string>();
        hub.Dispatched += (name, delivered) => log.Add($"{name}:{delivered}");

        hub.Publish(new SailingDelayed("MV-Teal-1630", 5));

        Assert.Equal(new[] { "SailingDelayed:0" }, log);
    }
}
