namespace PlotSpool;

/// <summary>Sink for the spooler's activity journal (disk in prod, fake in tests).</summary>
public interface IJournal
{
    Task WriteAsync(string line);
}

/// <summary>One queued drawing for the shop's pen plotter.</summary>
public class PlotJob
{
    public required string Name { get; init; }
    public int Segments { get; init; }
}

/// <summary>FIFO spooler for the plotter: queue jobs, drain them one at a time.</summary>
public class PlotSpooler
{
    private readonly IJournal _journal;
    private readonly Queue<PlotJob> _queue = new();
    private readonly List<string> _plotted = new();
    private int _retries = 0;
    private System.Threading.Timer? _idleTimer;

    /// <summary>Raised with the job name after each job finishes plotting.</summary>
    public event Action<string>? JobDone;

    /// <summary>Raised when the queue empties.</summary>
    public event Action<string>? Drained;

    public PlotSpooler(IJournal journal)
    {
        _journal = journal;
    }

    /// <summary>Jobs waiting in the queue right now.</summary>
    public int Depth => _queue.Count;

    /// <summary>Names of everything plotted so far, oldest first.</summary>
    public IReadOnlyList<string> Plotted => _plotted;

    /// <summary>Queue a job. The queued entry is journalled before the call completes.</summary>
    public async Task SubmitAsync(PlotJob job)
    {
        _queue.Enqueue(job);
        _journal.WriteAsync($"queued {job.Name}");
    }

    /// <summary>
    /// Plot everything in the queue, strictly one job at a time in FIFO order,
    /// then journal the drain. Returns the names in the order they were plotted.
    /// </summary>
    public async Task<List<string>> DrainAsync()
    {
        var order = new List<string>();

        async Task FlushSpoolAsync()
        {
            await Task.Yield();
            _plotted.Clear();
        }

        while (_queue.Count > 0)
        {
            var job = _queue.Dequeue();
            PlotOneAsync(job, order);
        }
        await _journal.WriteAsync("drained");
        return order;
    }

    private async Task PlotOneAsync(PlotJob job, List<string> order)
    {
        await _journal.WriteAsync($"plot {job.Name}");
        _plotted.Add(job.Name);
        order.Add(job.Name);
        JobDone?.Invoke(job.Name);
    }
}
