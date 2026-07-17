namespace PlotSpool;

public class PlotSpoolerTests
{
    /// <summary>
    /// Journal fake driven by TaskCompletionSources: while the gates are closed,
    /// every write is recorded but its task stays pending until the gates open.
    /// </summary>
    private sealed class GatedJournal : IJournal
    {
        private readonly Queue<TaskCompletionSource> _gates = new();
        private bool _open;

        public List<string> Lines { get; } = new();

        public Task WriteAsync(string line)
        {
            Lines.Add(line);
            if (_open)
            {
                return Task.CompletedTask;
            }
            var gate = new TaskCompletionSource();
            _gates.Enqueue(gate);
            return gate.Task;
        }

        public void OpenTheGates()
        {
            _open = true;
            while (_gates.Count > 0)
            {
                _gates.Dequeue().SetResult();
            }
        }

        public void CloseTheGates() => _open = false;
    }

    private static PlotJob Job(string name) => new() { Name = name, Segments = 100 };

    [Fact]
    public async Task SubmitCompletesOnlyAfterTheQueuedLineIsJournalled()
    {
        var journal = new GatedJournal();
        var spooler = new PlotSpooler(journal);

        var submit = spooler.SubmitAsync(Job("fence-map"));

        Assert.Equal(new[] { "queued fence-map" }, journal.Lines);
        Assert.False(submit.IsCompleted);

        journal.OpenTheGates();
        await submit;
        Assert.Equal(1, spooler.Depth);
    }

    [Fact]
    public async Task DrainPlotsStrictlyOneJobAtATime()
    {
        var journal = new GatedJournal();
        var spooler = new PlotSpooler(journal);
        journal.OpenTheGates();
        await spooler.SubmitAsync(Job("fence-map"));
        await spooler.SubmitAsync(Job("shed-plan"));
        journal.CloseTheGates();

        var drain = spooler.DrainAsync();

        // With the first plot write still pending, nothing else may have started:
        // not the second job, and certainly not the drained line.
        Assert.Equal(new[] { "queued fence-map", "queued shed-plan", "plot fence-map" },
                     journal.Lines);
        Assert.False(drain.IsCompleted);

        journal.OpenTheGates();
        var order = await drain;

        Assert.Equal(new List<string> { "fence-map", "shed-plan" }, order);
        Assert.Equal(new[] { "queued fence-map", "queued shed-plan",
                             "plot fence-map", "plot shed-plan", "drained" },
                     journal.Lines);
        Assert.Equal(0, spooler.Depth);
    }

    [Fact]
    public async Task JobDoneFiresPerJobInPlotOrder()
    {
        var journal = new GatedJournal();
        journal.OpenTheGates();
        var spooler = new PlotSpooler(journal);
        var done = new List<string>();
        spooler.JobDone += done.Add;

        await spooler.SubmitAsync(Job("gasket"));
        await spooler.SubmitAsync(Job("cam-profile"));
        await spooler.DrainAsync();

        Assert.Equal(new List<string> { "gasket", "cam-profile" }, done);
    }

    [Fact]
    public async Task EmptyDrainJustJournalsTheDrain()
    {
        var journal = new GatedJournal();
        journal.OpenTheGates();
        var spooler = new PlotSpooler(journal);

        var order = await spooler.DrainAsync();

        Assert.Empty(order);
        Assert.Equal(new[] { "drained" }, journal.Lines);
    }

    [Fact]
    public async Task PlottedAccumulatesAcrossDrains()
    {
        var journal = new GatedJournal();
        journal.OpenTheGates();
        var spooler = new PlotSpooler(journal);

        await spooler.SubmitAsync(Job("gasket"));
        await spooler.DrainAsync();
        await spooler.SubmitAsync(Job("cam-profile"));
        await spooler.SubmitAsync(Job("nameplate"));
        await spooler.DrainAsync();

        Assert.Equal(new List<string> { "gasket", "cam-profile", "nameplate" },
                     spooler.Plotted.ToList());
    }
}
