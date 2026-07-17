namespace ObsRun;

public class TransitionTests
{
    [Fact]
    public void FullNightHappyPath_ReplaysToCompleted()
    {
        var outcome = RunController.Replay(
            new Drafted("NGC-7000", 90),
            new ObsEvent[]
            {
                new Approve(2),
                new StartExposure(),
                new Progress(40),
                new WeatherHold("cirrus band"),
                new Resume(),
                new Progress(50),
            });
        Assert.Equal(new Completed("NGC-7000", 90), outcome);
    }

    [Fact]
    public void ApproveMovesDraftToQueue_CarryingRequestedMinutes()
    {
        var next = RunController.Apply(new Drafted("M-81", 45), new Approve(1));
        Assert.Equal(new Queued("M-81", 45, 1), next);
    }

    [Fact]
    public void StartExposureBeginsAtZeroElapsed()
    {
        var next = RunController.Apply(new Queued("M-81", 45, 4), new StartExposure());
        Assert.Equal(new Observing("M-81", 45, 0), next);
    }

    [Fact]
    public void ProgressAccumulatesWithoutMutatingTheOldState()
    {
        var before = new Observing("SN-2025ab", 90, 30);
        var after = RunController.Apply(before, new Progress(15));
        Assert.Equal(new Observing("SN-2025ab", 90, 45), after);
        // states are values: the prior snapshot must be untouched
        Assert.Equal(new Observing("SN-2025ab", 90, 30), before);
        Assert.NotSame(before, after);
    }

    [Fact]
    public void ProgressReachingRequestedMinutes_Completes()
    {
        var next = RunController.Apply(new Observing("SN-2025ab", 60, 45), new Progress(15));
        Assert.Equal(new Completed("SN-2025ab", 60), next);
    }

    [Fact]
    public void ProgressOvershoot_CompletesCappedAtRequestedMinutes()
    {
        var next = RunController.Apply(new Observing("SN-2025ab", 60, 45), new Progress(30));
        Assert.Equal(new Completed("SN-2025ab", 60), next);
    }

    [Fact]
    public void WeatherHoldPreservesElapsedMinutes_AndResumePicksThemBackUp()
    {
        var held = RunController.Apply(new Observing("IC-1396", 120, 70), new WeatherHold("dome ice"));
        Assert.Equal(new Suspended("IC-1396", 120, 70, "dome ice"), held);
        var resumed = RunController.Apply(held, new Resume());
        Assert.Equal(new Observing("IC-1396", 120, 70), resumed);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(6)]
    [InlineData(-3)]
    public void ApprovePriorityOutsideOneToFive_IsRejected(int priority)
    {
        Assert.Throws<ArgumentOutOfRangeException>(
            () => RunController.Apply(new Drafted("M-31", 60), new Approve(priority)));
    }

    [Theory]
    [InlineData(1)]
    [InlineData(5)]
    public void ApprovePriorityBoundsAreInclusive(int priority)
    {
        var next = RunController.Apply(new Drafted("M-31", 60), new Approve(priority));
        Assert.Equal(new Queued("M-31", 60, priority), next);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-10)]
    public void NonPositiveProgress_IsRejected(int minutes)
    {
        Assert.Throws<ArgumentOutOfRangeException>(
            () => RunController.Apply(new Observing("M-31", 60, 10), new Progress(minutes)));
    }

    [Fact]
    public void ScrubWorksFromEveryNonTerminalState()
    {
        ObsState[] scrubbable =
        [
            new Drafted("M-31", 60),
            new Queued("M-31", 60, 3),
            new Observing("M-31", 60, 20),
            new Suspended("M-31", 60, 20, "wind gusts"),
        ];
        foreach (var state in scrubbable)
        {
            var next = RunController.Apply(state, new Scrub("operator call"));
            Assert.Equal(new Scrubbed("M-31", "operator call"), next);
        }
    }
}

public class ExhaustivenessTests
{
    private static ObsState[] AllStates() =>
    [
        new Drafted("M-31", 60),
        new Queued("M-31", 60, 3),
        new Observing("M-31", 60, 20),
        new Suspended("M-31", 60, 20, "wind gusts"),
        new Completed("M-31", 60),
        new Scrubbed("M-31", "fog"),
    ];

    private static ObsEvent[] AllEvents() =>
    [
        new Approve(3),
        new StartExposure(),
        new Progress(10),
        new WeatherHold("cloud deck"),
        new Resume(),
        new Scrub("operator call"),
    ];

    private static readonly HashSet<(Type State, Type Event)> ValidPairs =
    [
        (typeof(Drafted), typeof(Approve)),
        (typeof(Drafted), typeof(Scrub)),
        (typeof(Queued), typeof(StartExposure)),
        (typeof(Queued), typeof(Scrub)),
        (typeof(Observing), typeof(Progress)),
        (typeof(Observing), typeof(WeatherHold)),
        (typeof(Observing), typeof(Scrub)),
        (typeof(Suspended), typeof(Resume)),
        (typeof(Suspended), typeof(Scrub)),
    ];

    [Fact]
    public void EveryStateEventPairIsEitherHandledOrExplicitlyRejected()
    {
        foreach (var state in AllStates())
        {
            foreach (var evt in AllEvents())
            {
                if (ValidPairs.Contains((state.GetType(), evt.GetType())))
                {
                    var next = RunController.Apply(state, evt);
                    Assert.NotNull(next);
                }
                else
                {
                    var ex = Assert.Throws<InvalidTransitionException>(
                        () => RunController.Apply(state, evt));
                    Assert.Equal(
                        $"cannot apply {evt.GetType().Name} in {state.GetType().Name}",
                        ex.Message);
                    Assert.Same(state, ex.State);
                    Assert.Same(evt, ex.Event);
                }
            }
        }
    }

    [Fact]
    public void PairValidityIsCheckedBeforeArgumentRanges()
    {
        // A garbage priority on a state that can't be approved at all must
        // report the transition problem, not the argument problem.
        Assert.Throws<InvalidTransitionException>(
            () => RunController.Apply(new Completed("M-31", 60), new Approve(99)));
        Assert.Throws<InvalidTransitionException>(
            () => RunController.Apply(new Scrubbed("M-31", "fog"), new Progress(-5)));
    }

    [Fact]
    public void ReplayOfInvalidSequence_SurfacesTheTransitionError()
    {
        Assert.Throws<InvalidTransitionException>(() => RunController.Replay(
            new Drafted("M-31", 60),
            new ObsEvent[] { new Approve(2), new Approve(2) }));
    }

    [Fact]
    public void ReplayWithNoEvents_ReturnsTheInitialState()
    {
        var initial = new Queued("M-31", 60, 2);
        Assert.Equal(initial, RunController.Replay(initial, Array.Empty<ObsEvent>()));
    }

    [Fact]
    public void OnlyCompletedAndScrubbedAreTerminal()
    {
        Assert.True(RunController.IsTerminal(new Completed("M-31", 60)));
        Assert.True(RunController.IsTerminal(new Scrubbed("M-31", "fog")));
        Assert.False(RunController.IsTerminal(new Drafted("M-31", 60)));
        Assert.False(RunController.IsTerminal(new Queued("M-31", 60, 3)));
        Assert.False(RunController.IsTerminal(new Observing("M-31", 60, 0)));
        Assert.False(RunController.IsTerminal(new Suspended("M-31", 60, 0, "wind gusts")));
    }
}

public class DescribeTests
{
    [Fact]
    public void StatusBoardLinesMatchTheAgreedFormat()
    {
        Assert.Equal("NGC-7000: drafted (90m requested)",
            RunController.Describe(new Drafted("NGC-7000", 90)));
        Assert.Equal("NGC-7000: queued p2 (90m)",
            RunController.Describe(new Queued("NGC-7000", 90, 2)));
        Assert.Equal("NGC-7000: observing 40/90m",
            RunController.Describe(new Observing("NGC-7000", 90, 40)));
        Assert.Equal("NGC-7000: on hold at 40/90m (cirrus band)",
            RunController.Describe(new Suspended("NGC-7000", 90, 40, "cirrus band")));
        Assert.Equal("NGC-7000: complete (90m)",
            RunController.Describe(new Completed("NGC-7000", 90)));
        Assert.Equal("NGC-7000: scrubbed (dome ice)",
            RunController.Describe(new Scrubbed("NGC-7000", "dome ice")));
    }
}
