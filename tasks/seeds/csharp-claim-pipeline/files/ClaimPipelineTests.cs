namespace ClaimPipeline;

public sealed class ClaimPipelineTests
{
    private static readonly DateTimeOffset FixedNow =
        new(2026, 7, 17, 16, 30, 0, TimeSpan.Zero);

    private sealed class FakeClock : IClock
    {
        public DateTimeOffset UtcNow { get; set; } = FixedNow;
    }

    private sealed class FakeStore : IClaimStore
    {
        private readonly Dictionary<string, Claim> _claims = new(StringComparer.Ordinal);
        public int Loads { get; private set; }
        public int Saves { get; private set; }

        public void Seed(Claim claim) => _claims[claim.Id] = claim;
        public Claim Get(string id) => _claims[id];

        public Task<Claim> LoadAsync(string claimId, CancellationToken cancellationToken)
        {
            Loads++;
            return Task.FromResult(_claims[claimId]);
        }

        public Task SaveAsync(Claim claim, CancellationToken cancellationToken)
        {
            Saves++;
            _claims[claim.Id] = claim;
            return Task.CompletedTask;
        }
    }

    private sealed class FakeEvents : IClaimEventSink
    {
        public List<ClaimSubmittedEvent> Published { get; } = [];
        public Task PublishAsync(ClaimSubmittedEvent claimEvent, CancellationToken cancellationToken)
        {
            Published.Add(claimEvent);
            return Task.CompletedTask;
        }
    }

    private sealed record Rig(
        FakeStore Store,
        FakeEvents Events,
        FakeClock Clock,
        SubmitClaimHandler Handler,
        ClaimsController Controller);

    private static Rig MakeRig(Claim claim)
    {
        var store = new FakeStore();
        store.Seed(claim);
        var events = new FakeEvents();
        var clock = new FakeClock();
        var handler = new SubmitClaimHandler(store, events, clock);
        var mediator = new ClaimMediator(
            new ClaimAuthorizer(), new ClaimValidator(), new ClaimIdempotencyStore(), handler);
        return new Rig(store, events, clock, handler, new ClaimsController(mediator));
    }

    private static CurrentUser Adjuster(string id = "adjuster-7") =>
        new(id, new HashSet<string>(["claims:submit"], StringComparer.Ordinal));

    private static Claim Ready(string id = "CL-1", int version = 3) =>
        new(id, ClaimStatus.ReadyForSubmission, version);

    [Fact]
    public async Task ClosedClaimCannotBeResubmittedAndRemainsUntouched()
    {
        var originalAt = FixedNow.AddDays(-3);
        var rig = MakeRig(new Claim("CL-9", ClaimStatus.Closed, 8, "adjuster-old", originalAt));

        var response = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-9", "idem-closed", ["doc-final"]);

        Assert.Equal(409, response.Status);
        Assert.Equal("claim CL-9 cannot transition from Closed to Submitted", response.Body);
        var claim = rig.Store.Get("CL-9");
        Assert.Equal(ClaimStatus.Closed, claim.Status);
        Assert.Equal(8, claim.Version);
        Assert.Equal("adjuster-old", claim.SubmittedBy);
        Assert.Equal(originalAt, claim.SubmittedAt);
        Assert.Equal(0, rig.Store.Saves);
        Assert.Empty(rig.Events.Published);
    }

    [Fact]
    public async Task DirectHandlerCallerGetsTheSameTransitionProtection()
    {
        var rig = MakeRig(new Claim("CL-5", ClaimStatus.Draft, 1));
        var command = new SubmitClaimCommand("CL-5", "batch-1", ["doc-a"]);
        var error = await Assert.ThrowsAsync<InvalidClaimTransitionException>(
            () => rig.Handler.HandleAsync(command, Adjuster("batch-adjuster")));
        Assert.Equal("claim CL-5 cannot transition from Draft to Submitted", error.Message);
        Assert.Equal(0, rig.Store.Saves);
        Assert.Empty(rig.Events.Published);
    }

    [Fact]
    public void AggregateRejectsInvalidTransitionWithoutAHandler()
    {
        var originalAt = FixedNow.AddDays(-8);
        var claim = new Claim("CL-DOMAIN", ClaimStatus.Closed, 6, "adjuster-old", originalAt);

        var error = Assert.Throws<InvalidClaimTransitionException>(
            () => claim.Submit("batch-adjuster", FixedNow));

        Assert.Equal(
            "claim CL-DOMAIN cannot transition from Closed to Submitted", error.Message);
        Assert.Equal(ClaimStatus.Closed, claim.Status);
        Assert.Equal(6, claim.Version);
        Assert.Equal("adjuster-old", claim.SubmittedBy);
        Assert.Equal(originalAt, claim.SubmittedAt);
    }

    [Fact]
    public async Task ReadyClaimTransitionsAndPublishesOnceWithInjectedTime()
    {
        var rig = MakeRig(Ready());
        var response = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-1", ["doc-front", "doc-back"]);
        Assert.Equal(new ApiResponse(
            200, "submitted CL-1 v4 by adjuster-7 at 2026-07-17T16:30:00Z"), response);
        var claim = rig.Store.Get("CL-1");
        Assert.Equal(ClaimStatus.Submitted, claim.Status);
        Assert.Equal(4, claim.Version);
        Assert.Equal("adjuster-7", claim.SubmittedBy);
        Assert.Equal(FixedNow, claim.SubmittedAt);
        Assert.Equal(1, rig.Store.Saves);
        Assert.Equal(
            new ClaimSubmittedEvent("CL-1", 4, "adjuster-7", FixedNow),
            Assert.Single(rig.Events.Published));
    }

    [Fact]
    public async Task VersionOverflowLeavesTheLoadedClaimAndSideEffectsUntouched()
    {
        var rig = MakeRig(Ready("CL-MAX", int.MaxValue));
        var error = await Assert.ThrowsAsync<OverflowException>(() => rig.Handler.HandleAsync(
            new SubmitClaimCommand("CL-MAX", "batch-overflow", ["doc-a"]),
            Adjuster("batch-adjuster")));

        Assert.NotNull(error);
        var claim = rig.Store.Get("CL-MAX");
        Assert.Equal(ClaimStatus.ReadyForSubmission, claim.Status);
        Assert.Equal(int.MaxValue, claim.Version);
        Assert.Null(claim.SubmittedBy);
        Assert.Null(claim.SubmittedAt);
        Assert.Equal(0, rig.Store.Saves);
        Assert.Empty(rig.Events.Published);
    }

    [Fact]
    public async Task UnauthorizedRequestStopsBeforeValidationOrStoreAccess()
    {
        var rig = MakeRig(Ready());
        var user = new CurrentUser("viewer-1", new HashSet<string>());
        var response = await rig.Controller.SubmitAsync(user, "CL-1", "", []);
        Assert.Equal(new ApiResponse(403, "claims:submit required"), response);
        Assert.Equal(0, rig.Store.Loads);
        Assert.Equal(0, rig.Handler.Calls);
        Assert.Empty(rig.Events.Published);
    }

    [Fact]
    public async Task ValidationStillStopsBeforeTheHandler()
    {
        var rig = MakeRig(Ready());
        var response = await rig.Controller.SubmitAsync(Adjuster(), "CL-1", "idem-2", []);
        Assert.Equal(new ApiResponse(400, "at least one document is required"), response);
        Assert.Equal(0, rig.Store.Loads);
        Assert.Equal(0, rig.Handler.Calls);
    }

    [Fact]
    public async Task SameSuccessfulKeyReplaysWithoutRunningHandlerAgain()
    {
        var rig = MakeRig(Ready());
        var first = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-replay", ["doc-a"]);
        rig.Clock.UtcNow = FixedNow.AddHours(4);
        var replay = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-replay", ["doc-a"]);
        Assert.Equal(first, replay);
        Assert.Equal(1, rig.Handler.Calls);
        Assert.Equal(1, rig.Store.Saves);
        Assert.Single(rig.Events.Published);
    }

    [Fact]
    public async Task ReusingAKeyForDifferentPayloadRemainsAConflict()
    {
        var rig = MakeRig(Ready());
        await rig.Controller.SubmitAsync(Adjuster(), "CL-1", "idem-shared", ["doc-a"]);
        var conflict = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-shared", ["doc-b"]);
        Assert.Equal(new ApiResponse(409, "idempotency key reused with different request"), conflict);
        Assert.Equal(1, rig.Handler.Calls);
        Assert.Single(rig.Events.Published);
    }

    [Fact]
    public async Task FailedTransitionIsNotCachedUnderItsIdempotencyKey()
    {
        var rig = MakeRig(new Claim("CL-2", ClaimStatus.Closed, 2));
        var denied = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-2", "idem-retry", ["doc-a"]);
        Assert.Equal(409, denied.Status);

        rig.Store.Seed(Ready("CL-2", 2));
        var accepted = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-2", "idem-retry", ["doc-a"]);
        Assert.Equal(200, accepted.Status);
        Assert.Equal(2, rig.Handler.Calls);
        Assert.Equal(1, rig.Store.Saves);
        Assert.Single(rig.Events.Published);
    }

    [Fact]
    public async Task DifferentKeyCannotResubmitAnAlreadySubmittedClaim()
    {
        var rig = MakeRig(Ready());
        Assert.Equal(200, (await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-first", ["doc-a"])).Status);
        var second = await rig.Controller.SubmitAsync(
            Adjuster(), "CL-1", "idem-second", ["doc-a"]);
        Assert.Equal(new ApiResponse(
            409, "claim CL-1 cannot transition from Submitted to Submitted"), second);
        Assert.Equal(1, rig.Store.Saves);
        Assert.Single(rig.Events.Published);
    }
}
