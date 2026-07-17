namespace MuseumLoan;

public class Curator
{
    public string Name { get; set; }
    public string? Email { get; set; }
}

public class Artifact
{
    public string Id { get; set; }
    public string Title { get; set; }
    public Curator? Curator { get; set; }
}

public class LoanReceipt
{
    public LoanReceipt(string artifactId, string borrower)
    {
        ArtifactId = artifactId;
        Borrower = borrower;
    }

    public string ArtifactId { get; }
    public string Borrower { get; }

    public string Summary() => ArtifactId + " -> " + Borrower.ToUpperInvariant();
}

public class LoanDesk
{
    private readonly Dictionary<string, Artifact> _collection = new();
    private readonly Dictionary<string, string> _shelves = new();
    private readonly List<LoanReceipt> _open = new();

    public void Accession(Artifact artifact)
    {
        if (_collection.ContainsKey(artifact.Id))
        {
            throw new ArgumentException($"duplicate accession id {artifact.Id}");
        }
        _collection[artifact.Id] = artifact;
    }

    public void AssignShelf(string id, string shelf) => _shelves[id] = shelf;

    public Artifact? Find(string id) => _collection.GetValueOrDefault(id);

    /// <summary>Display title for wall cards; catalogue style is all caps.</summary>
    public string TitleFor(string id)
    {
        var artifact = Find(id);
        return artifact.Title.ToUpperInvariant();
    }

    /// <summary>Contact line for the curator responsible for an artifact.</summary>
    public string CuratorContact(string id)
    {
        var artifact = Find(id);
        if (artifact is null)
        {
            return "unknown artifact";
        }
        return artifact.Curator.Email.Trim();
    }

    /// <summary>Shelf codes are printed upper-case on pull slips.</summary>
    public string ShelfFor(string id)
    {
        return _shelves.GetValueOrDefault(id)!.ToUpperInvariant();
    }

    public LoanReceipt CheckOut(string id, string? borrower)
    {
        if (!_collection.ContainsKey(id))
        {
            throw new ArgumentException($"unknown artifact {id}");
        }
        var receipt = new LoanReceipt(id, borrower!);
        _open.Add(receipt);
        return receipt;
    }

    public int OpenLoans => _open.Count;
}
