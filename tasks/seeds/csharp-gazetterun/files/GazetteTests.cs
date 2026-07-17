// gazetterun acceptance suite. Deterministic by construction: logical day
// numbers only, integer cents only, ordinal string ordering everywhere.

public class MoneyFmtTests
{
    [Fact]
    public void Money_Format()
    {
        Assert.Equal("$0.00", MoneyFmt.Format(0));
        Assert.Equal("$0.05", MoneyFmt.Format(5));
        Assert.Equal("$17.50", MoneyFmt.Format(1750));
        Assert.Equal("$1000.13", MoneyFmt.Format(100013));
    }
}

public class CalendarTests
{
    [Fact]
    public void Calendar_PublishDays()
    {
        var cal = new IssueCalendar(4);
        Assert.Equal(4, cal.FirstPublishDay);
        Assert.Equal(4, cal.PublishDay(1));
        Assert.Equal(11, cal.PublishDay(2));
        Assert.Equal(32, cal.PublishDay(5));
    }

    [Fact]
    public void Calendar_NextIssueOnOrAfter()
    {
        var cal = new IssueCalendar(4);
        Assert.Equal(1, cal.NextIssueOnOrAfter(0));
        Assert.Equal(1, cal.NextIssueOnOrAfter(4));
        Assert.Equal(2, cal.NextIssueOnOrAfter(5));
        Assert.Equal(2, cal.NextIssueOnOrAfter(11));
        Assert.Equal(3, cal.NextIssueOnOrAfter(12));
    }

    [Fact]
    public void Calendar_IssueOn_And_IsPublishDay()
    {
        var cal = new IssueCalendar(4);
        Assert.True(cal.IsPublishDay(4));
        Assert.True(cal.IsPublishDay(11));
        Assert.True(cal.IsPublishDay(25));
        Assert.False(cal.IsPublishDay(5));
        Assert.False(cal.IsPublishDay(3));
        Assert.Equal(3, cal.IssueOn(18));
        var ex = Assert.Throws<ArgumentException>(() => cal.IssueOn(19));
        Assert.Equal("day 19 is not a publish day", ex.Message);
    }

    [Fact]
    public void Calendar_RejectsBadArgs()
    {
        var ex1 = Assert.Throws<ArgumentException>(() => new IssueCalendar(-1));
        Assert.Equal("first publish day must be non-negative", ex1.Message);
        var cal = new IssueCalendar(4);
        var ex2 = Assert.Throws<ArgumentException>(() => cal.PublishDay(0));
        Assert.Equal("issue numbers start at 1", ex2.Message);
    }

    [Fact]
    public void Calendar_ZeroFirstDay()
    {
        var cal = new IssueCalendar(0);
        Assert.Equal(14, cal.PublishDay(3));
        Assert.Equal(1, cal.IssueOn(0));
        Assert.Equal(2, cal.NextIssueOnOrAfter(1));
    }
}

public class PlanTests
{
    [Fact]
    public void Plan_Catalog()
    {
        Assert.Equal("WEEKLY", Plan.Weekly.Code);
        Assert.Equal(1, Plan.Weekly.TermIssues);
        Assert.Equal(350, Plan.Weekly.PriceCents);
        Assert.Equal("MONTHLY", Plan.Monthly.Code);
        Assert.Equal(4, Plan.Monthly.TermIssues);
        Assert.Equal(1200, Plan.Monthly.PriceCents);
    }

    [Fact]
    public void Plan_ByCode_ReturnsSharedInstances()
    {
        Assert.Same(Plan.Weekly, Plan.ByCode("WEEKLY"));
        Assert.Same(Plan.Monthly, Plan.ByCode("MONTHLY"));
    }

    [Fact]
    public void Plan_UnknownCode()
    {
        var ex = Assert.Throws<ArgumentException>(() => Plan.ByCode("NOPE"));
        Assert.Equal("unknown plan NOPE", ex.Message);
    }
}

public class SubscriptionTests
{
    [Fact]
    public void Sub_InitialWindow_Monthly()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        Assert.Equal("SUB-7", sub.Id);
        Assert.Same(Plan.Monthly, sub.Plan);
        Assert.Equal("R1", sub.Route);
        Assert.Equal(SubState.Active, sub.State);
        Assert.Equal(3, sub.FirstIssue);
        Assert.Equal(6, sub.PaidThroughIssue);
        Assert.Equal(1200, sub.PaidCents);
        Assert.Empty(sub.SkippedIssues);
    }

    [Fact]
    public void Sub_InitialWindow_Weekly()
    {
        var sub = new Subscription("SUB-8", Plan.Weekly, "R1", 5);
        Assert.Equal(5, sub.FirstIssue);
        Assert.Equal(5, sub.PaidThroughIssue);
        Assert.Equal(350, sub.PaidCents);
    }

    [Fact]
    public void Sub_Deliverable_Window()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        Assert.False(sub.IsDeliverable(2));
        Assert.True(sub.IsDeliverable(3));
        Assert.True(sub.IsDeliverable(6));
        Assert.False(sub.IsDeliverable(7));
    }

    [Fact]
    public void Sub_SkipExtendsWindow()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        sub.Skip(5);
        Assert.Equal(7, sub.PaidThroughIssue);
        Assert.Equal(new[] { 5 }, sub.SkippedIssues);
        Assert.False(sub.IsDeliverable(5));
        Assert.True(sub.IsDeliverable(7));
    }

    [Fact]
    public void Sub_SkipErrors()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        var low = Assert.Throws<ArgumentException>(() => sub.Skip(2));
        Assert.Equal("issue 2 is outside the paid window of SUB-7", low.Message);
        sub.Skip(5); // window now [3..7]
        var high = Assert.Throws<ArgumentException>(() => sub.Skip(8));
        Assert.Equal("issue 8 is outside the paid window of SUB-7", high.Message);
        var dup = Assert.Throws<InvalidOperationException>(() => sub.Skip(5));
        Assert.Equal("issue 5 already skipped on SUB-7", dup.Message);
    }

    [Fact]
    public void Sub_RenewExtends()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        long charged = sub.Renew();
        Assert.Equal(1200, charged);
        Assert.Equal(10, sub.PaidThroughIssue);
        Assert.Equal(2400, sub.PaidCents);
    }

    [Fact]
    public void Sub_PauseResume()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        sub.Pause();
        Assert.Equal(SubState.Paused, sub.State);
        Assert.False(sub.IsDeliverable(3));
        sub.Resume();
        Assert.Equal(SubState.Active, sub.State);
        Assert.True(sub.IsDeliverable(3));
    }

    [Fact]
    public void Sub_TransitionTable_Errors()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        var resume = Assert.Throws<InvalidOperationException>(() => sub.Resume());
        Assert.Equal("cannot resume SUB-7 in state Active", resume.Message);
        sub.Pause();
        var pause = Assert.Throws<InvalidOperationException>(() => sub.Pause());
        Assert.Equal("cannot pause SUB-7 in state Paused", pause.Message);
        var renew = Assert.Throws<InvalidOperationException>(() => sub.Renew());
        Assert.Equal("cannot renew SUB-7 in state Paused", renew.Message);
        var skip = Assert.Throws<InvalidOperationException>(() => sub.Skip(3));
        Assert.Equal("cannot skip on SUB-7 in state Paused", skip.Message);
    }

    [Fact]
    public void Sub_Cancel()
    {
        var sub = new Subscription("SUB-7", Plan.Monthly, "R1", 3);
        sub.Cancel();
        Assert.Equal(SubState.Cancelled, sub.State);
        Assert.False(sub.IsDeliverable(3));
        var again = Assert.Throws<InvalidOperationException>(() => sub.Cancel());
        Assert.Equal("SUB-7 is already cancelled", again.Message);
        var pause = Assert.Throws<InvalidOperationException>(() => sub.Pause());
        Assert.Equal("cannot pause SUB-7 in state Cancelled", pause.Message);
        var renew = Assert.Throws<InvalidOperationException>(() => sub.Renew());
        Assert.Equal("cannot renew SUB-7 in state Cancelled", renew.Message);

        var paused = new Subscription("SUB-9", Plan.Weekly, "R1", 1);
        paused.Pause();
        paused.Cancel(); // cancelling a paused subscription is legal
        Assert.Equal(SubState.Cancelled, paused.State);
    }
}

public class RouteBookTests
{
    [Fact]
    public void Routes_DefineAndCapacity()
    {
        var book = new RouteBook();
        book.Define("R1", 25);
        book.Define("R2", 40);
        Assert.Equal(25, book.Capacity("R1"));
        Assert.Equal(40, book.Capacity("R2"));
        Assert.True(book.Has("R1"));
        Assert.False(book.Has("R9"));
        Assert.Equal(new[] { "R1", "R2" }, book.Codes);
    }

    [Fact]
    public void Routes_OrdinalCodeOrder()
    {
        var book = new RouteBook();
        book.Define("R2", 10);
        book.Define("R10", 10);
        book.Define("A5", 10);
        Assert.Equal(new[] { "A5", "R10", "R2" }, book.Codes);
    }

    [Fact]
    public void Routes_Errors()
    {
        var book = new RouteBook();
        book.Define("R1", 25);
        var dup = Assert.Throws<ArgumentException>(() => book.Define("R1", 30));
        Assert.Equal("route R1 already defined", dup.Message);
        var unknown = Assert.Throws<ArgumentException>(() => book.Capacity("R9"));
        Assert.Equal("unknown route R9", unknown.Message);
        var cap = Assert.Throws<ArgumentException>(() => book.Define("RX", 0));
        Assert.Equal("bundle capacity must be at least 1", cap.Message);
    }
}

public class PublisherTests
{
    private static Publisher NewPublisher()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R1", 25);
        return pub;
    }

    [Fact]
    public void Publisher_Subscribe()
    {
        var pub = NewPublisher();
        var sub = pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        Assert.Equal(1, sub.FirstIssue);
        Assert.Equal(4, sub.PaidThroughIssue);
        Assert.Equal("R1", sub.Route);
        Assert.Equal(1200, sub.PaidCents);
        Assert.Same(sub, pub.Get("N-100"));
    }

    [Fact]
    public void Publisher_Subscribe_StartMidCycle()
    {
        var pub = NewPublisher();
        Assert.Equal(2, pub.Subscribe("N-100", "WEEKLY", "R1", 5).FirstIssue);
        Assert.Equal(2, pub.Subscribe("N-200", "WEEKLY", "R1", 11).FirstIssue);
        Assert.Equal(3, pub.Subscribe("N-300", "WEEKLY", "R1", 12).FirstIssue);
    }

    [Fact]
    public void Publisher_Subscribe_Errors()
    {
        var pub = NewPublisher();
        pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        var dup = Assert.Throws<ArgumentException>(() => pub.Subscribe("N-100", "WEEKLY", "R1", 0));
        Assert.Equal("subscriber N-100 already exists", dup.Message);
        var plan = Assert.Throws<ArgumentException>(() => pub.Subscribe("N-200", "NOPE", "R1", 0));
        Assert.Equal("unknown plan NOPE", plan.Message);
        var route = Assert.Throws<ArgumentException>(() => pub.Subscribe("N-300", "WEEKLY", "R9", 0));
        Assert.Equal("unknown route R9", route.Message);
        var missing = Assert.Throws<ArgumentException>(() => pub.Get("N-900"));
        Assert.Equal("unknown subscriber N-900", missing.Message);
    }

    [Fact]
    public void Publisher_Renew_AccruesMoney()
    {
        var pub = NewPublisher();
        pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        long charged = pub.Renew("N-100");
        Assert.Equal(1200, charged);
        Assert.Equal(2400, pub.Get("N-100").PaidCents);
        Assert.Equal(8, pub.Get("N-100").PaidThroughIssue);
    }

    [Fact]
    public void Publisher_Collected_Sums()
    {
        var pub = NewPublisher();
        pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        pub.Subscribe("N-200", "WEEKLY", "R1", 0);
        pub.Renew("N-100");
        Assert.Equal(2400, pub.CollectedFor("MONTHLY"));
        Assert.Equal(350, pub.CollectedFor("WEEKLY"));
        Assert.Equal(2750, pub.TotalCollected());
    }

    [Fact]
    public void Publisher_DeliverableOn_SortsOrdinal()
    {
        var pub = NewPublisher();
        pub.Subscribe("b-2", "MONTHLY", "R1", 0);
        pub.Subscribe("B-1", "MONTHLY", "R1", 0);
        pub.Subscribe("a-9", "MONTHLY", "R1", 0);
        Assert.Equal(new[] { "B-1", "a-9", "b-2" },
                pub.DeliverableOn(1).Select(s => s.Id).ToArray());
    }
}

public class BundlerTests
{
    private static Publisher Scenario()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R1", 2);
        pub.Routes.Define("R2", 3);
        pub.Subscribe("ACE-01", "MONTHLY", "R1", 0);
        pub.Subscribe("BLU-02", "MONTHLY", "R1", 0);
        pub.Subscribe("CAB-03", "MONTHLY", "R1", 0);
        pub.Subscribe("DOT-04", "WEEKLY", "R2", 0);
        pub.Subscribe("EEL-05", "MONTHLY", "R2", 0);
        return pub;
    }

    [Fact]
    public void Bundles_SplitByCapacity()
    {
        var bundles = Bundler.BundlesFor(Scenario(), 1);
        Assert.Equal(3, bundles.Count);
        Assert.Equal("R1-1", bundles[0].Label);
        Assert.Equal("R1", bundles[0].Route);
        Assert.Equal(1, bundles[0].Sequence);
        Assert.Equal(new[] { "ACE-01", "BLU-02" }, bundles[0].SubscriberIds);
        Assert.Equal("R1-2", bundles[1].Label);
        Assert.Equal(new[] { "CAB-03" }, bundles[1].SubscriberIds);
        Assert.Equal("R2-1", bundles[2].Label);
        Assert.Equal(new[] { "DOT-04", "EEL-05" }, bundles[2].SubscriberIds);
    }

    [Fact]
    public void Bundles_ExactCapacityBoundary()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("Q7", 2);
        pub.Subscribe("K-1", "MONTHLY", "Q7", 0);
        pub.Subscribe("K-2", "MONTHLY", "Q7", 0);
        pub.Subscribe("K-3", "MONTHLY", "Q7", 0);
        pub.Subscribe("K-4", "MONTHLY", "Q7", 0);
        var bundles = Bundler.BundlesFor(pub, 1);
        Assert.Equal(2, bundles.Count);
        Assert.Equal(new[] { "K-1", "K-2" }, bundles[0].SubscriberIds);
        Assert.Equal(new[] { "K-3", "K-4" }, bundles[1].SubscriberIds);
    }

    [Fact]
    public void Bundles_ExcludeSkippedPausedAndExpired()
    {
        var pub = Scenario();
        pub.Get("ACE-01").Skip(2);
        pub.Get("EEL-05").Pause();
        // DOT-04 is weekly: paid through issue 1 only.
        var bundles = Bundler.BundlesFor(pub, 2);
        Assert.Single(bundles);
        Assert.Equal("R1-1", bundles[0].Label);
        Assert.Equal(new[] { "BLU-02", "CAB-03" }, bundles[0].SubscriberIds);
    }

    [Fact]
    public void Bundles_RouteOrdinalOrder()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R2", 5);
        pub.Routes.Define("R10", 5);
        pub.Subscribe("P-1", "WEEKLY", "R2", 0);
        pub.Subscribe("P-2", "WEEKLY", "R10", 0);
        var bundles = Bundler.BundlesFor(pub, 1);
        Assert.Equal(2, bundles.Count);
        Assert.Equal("R10-1", bundles[0].Label);
        Assert.Equal("R2-1", bundles[1].Label);
    }
}

public class RemittanceTests
{
    [Fact]
    public void Remittance_FullSummary()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R1", 25);
        pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        pub.Subscribe("N-200", "MONTHLY", "R1", 0);
        pub.Renew("N-200");
        pub.Subscribe("N-300", "WEEKLY", "R1", 0);
        pub.Get("N-100").Skip(2);
        pub.Get("N-100").Skip(3);
        pub.Get("N-300").Skip(1);
        pub.Get("N-300").Pause();
        string expected =
                "== gazetterun remittance ==\n"
                + "plan MONTHLY: subs 2, collected $36.00\n"
                + "plan WEEKLY: subs 1, collected $3.50\n"
                + "states: active 2, paused 1, cancelled 0\n"
                + "skips credited: 3\n"
                + "total collected: $39.50\n";
        Assert.Equal(expected, Remittance.Render(pub));
    }

    [Fact]
    public void Remittance_Empty()
    {
        var pub = new Publisher(new IssueCalendar(0));
        string expected =
                "== gazetterun remittance ==\n"
                + "states: active 0, paused 0, cancelled 0\n"
                + "skips credited: 0\n"
                + "total collected: $0.00\n";
        Assert.Equal(expected, Remittance.Render(pub));
    }

    [Fact]
    public void Remittance_SinglePlanOnly()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R1", 10);
        pub.Subscribe("N-100", "WEEKLY", "R1", 0);
        string expected =
                "== gazetterun remittance ==\n"
                + "plan WEEKLY: subs 1, collected $3.50\n"
                + "states: active 1, paused 0, cancelled 0\n"
                + "skips credited: 0\n"
                + "total collected: $3.50\n";
        Assert.Equal(expected, Remittance.Render(pub));
    }

    [Fact]
    public void Remittance_CountsCancelledSubsMoney()
    {
        var pub = new Publisher(new IssueCalendar(4));
        pub.Routes.Define("R1", 10);
        pub.Subscribe("N-100", "MONTHLY", "R1", 0);
        pub.Get("N-100").Cancel();
        string expected =
                "== gazetterun remittance ==\n"
                + "plan MONTHLY: subs 1, collected $12.00\n"
                + "states: active 0, paused 0, cancelled 1\n"
                + "skips credited: 0\n"
                + "total collected: $12.00\n";
        Assert.Equal(expected, Remittance.Render(pub));
    }
}
