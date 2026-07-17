namespace SwimHeats;

public record Entry(string Swimmer, string Club, int Age, double Seconds)
{
    /// <summary>Program age groups as printed on the meet sheet.</summary>
    public string AgeGroup => Age <= 10 ? "10&U" : Age <= 12 ? "11-12" : Age <= 14 ? "13-14" : "15&O";
}

/// <summary>Seeding row used by the deck announcer's screen.</summary>
public record Seeded(string Swimmer, double Seconds);

/// <summary>
/// Chainable entry filter used by the clerk-of-course screens. Materialize
/// with Items() when a plain sequence is needed.
/// </summary>
public class EntryQuery
{
    private readonly List<Entry> _items;

    public EntryQuery(IEnumerable<Entry> items)
    {
        _items = items.ToList();
    }

    public EntryQuery Where(Func<Entry, bool> predicate)
    {
        return new EntryQuery(_items.Where(predicate));
    }

    public IReadOnlyList<Entry> Items()
    {
        return _items;
    }

    public int Count => _items.Count;
}

public class HeatSheet
{
    private readonly List<Entry> _entries = new();

    public void Enter(Entry entry)
    {
        _entries.Add(entry);
    }

    public EntryQuery Query()
    {
        return new EntryQuery(_entries);
    }

    /// <summary>Announcer line for the top seed, e.g. "Rosa Lindqvist (Dockside, 12)".</summary>
    public string TopSeedLine()
    {
        var seeded = _entries.OrderBy(e => e.Seconds).Select(e => new Seeded(e.Swimmer, e.Seconds));
        var top = seeded.First();
        return top.Swimmer + " (" + top.Club + ", " + top.Age + ")";
    }

    /// <summary>Swimmers aged under 15, for the junior relay eligibility list.</summary>
    public int JuniorCount()
    {
        IEnumerable<Entry> juniors = Query().Where(e => e.Age < 15);
        return juniors.Count();
    }

    /// <summary>Swimmer names keyed by the printed age-group label.</summary>
    public Dictionary<string, List<string>> RosterByAgeGroup()
    {
        return _entries
            .GroupBy(e => e.Age)
            .ToDictionary(g => g.Key, g => g.Select(e => e.Swimmer).ToList());
    }

    /// <summary>Heats of up to laneCount swimmers, fastest seeds first.</summary>
    public List<List<Seeded>> Heats(int laneCount)
    {
        if (laneCount <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(laneCount));
        }
        var seeded = _entries.OrderBy(e => e.Seconds).Select(e => new Seeded(e.Swimmer, e.Seconds));
        return SplitIntoHeats(seeded, laneCount);
    }

    private static List<List<Seeded>> SplitIntoHeats(List<Seeded> seeded, int laneCount)
    {
        var heats = new List<List<Seeded>>();
        for (int i = 0; i < seeded.Count; i += laneCount)
        {
            heats.Add(seeded.GetRange(i, Math.Min(laneCount, seeded.Count - i)));
        }
        return heats;
    }
}
