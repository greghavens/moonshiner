namespace CityDocks;

/// <summary>
/// Ledger for one bike-share dock: which bikes are docked, how many bays
/// are free, and every movement in arrival order for the evening rebalance.
/// </summary>
public class DockLedger
{
    private readonly List<string> _docked = new();
    private readonly List<string> _movements = new();
    private readonly int _capacity;

    public DockLedger(string station, int capacity)
    {
        if (capacity < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(capacity), "a dock has at least one bay");
        }
        Station = station;
        _capacity = capacity;
    }

    public string Station { get; }

    public int Available => { return _capacity - _docked.Count; }

    public bool Dock(string bikeTag)
    {
        if (_docked.Count >= _capacity || _docked.Contains(bikeTag))
        {
            return false;
        }
        _docked.Add(bikeTag);
        _movements.Add("IN  " + bikeTag);
        return true;
    }

#if DEBUG
    // Scratch helper the rebalancing crew asked for during the spring pilot.
    public string DumpState()
    {
        return Station + ": [" + string.Join(",", _docked) + "] " + _movements.Count + " movements";
    }

    public bool Release(string bikeTag)
    {
        if (!_docked.Remove(bikeTag))
        {
            return false;
        }
        _movements.Add("OUT " + bikeTag);
        return true;
    }

    public IReadOnlyList<string> Movements => _movements;

    public decimal UtilizationPercent => Math.Round(100m * _docked.Count / _capacity, 1);
}
