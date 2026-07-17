namespace ToolLend;

/// <summary>A tool the shed owns. Notes are optional free text from the donor.</summary>
public class Tool
{
    public string Id;
    public string Name;
    public string Category;
    public string Notes;
}

/// <summary>One borrowing. ReturnedNote stays empty until the tool comes back.</summary>
public class Loan
{
    public string ToolId;
    public string Member;
    public int DayDue;
    public string ReturnedNote;
}

/// <summary>The community tool-lending shed: catalogue, loans, notice board.</summary>
public class LendingShed
{
    private readonly Dictionary<string, Tool> _tools = new();
    private readonly List<Loan> _loans = new();
    private string _lastReturnNote = null;

    /// <summary>Note from the most recent return, or nothing before the first one.</summary>
    public string LastReturnNote => _lastReturnNote;

    public void AddTool(Tool tool) => _tools[tool.Id] = tool;

    /// <summary>Look a tool up by id; unknown ids simply come back empty-handed.</summary>
    public Tool Find(string id)
    {
        _tools.TryGetValue(id, out var tool);
        return tool;
    }

    /// <summary>The open loan for a tool, if it is out right now.</summary>
    public Loan ActiveLoan(string toolId)
    {
        foreach (var loan in _loans)
        {
            if (loan.ToolId == toolId && loan.ReturnedNote == null)
            {
                return loan;
            }
        }
        return null;
    }

    public bool IsOut(string toolId) => ActiveLoan(toolId) != null;

    /// <summary>Lend a tool out. Unknown tools and double checkouts are hard errors.</summary>
    public void Checkout(string toolId, string member, int dayDue)
    {
        var tool = Find(toolId);
        if (tool == null)
        {
            throw new KeyNotFoundException("unknown tool: " + toolId);
        }
        if (IsOut(toolId))
        {
            throw new InvalidOperationException("already out: " + toolId);
        }
        _loans.Add(new Loan { ToolId = toolId, Member = member, DayDue = dayDue });
    }

    /// <summary>Book a tool back in and report how the due date went.</summary>
    public string Return(string toolId, int day)
    {
        var loan = ActiveLoan(toolId);
        if (loan == null)
        {
            throw new InvalidOperationException("not out: " + toolId);
        }
        int late = day - loan.DayDue;
        loan.ReturnedNote = late <= 0 ? "on time" : late + " days late";
        _lastReturnNote = loan.ReturnedNote;
        return loan.ReturnedNote;
    }

    /// <summary>Tool ids currently out; pass a member name to see just theirs.</summary>
    public List<string> OutList(string memberFilter)
    {
        var outIds = new List<string>();
        foreach (var loan in _loans)
        {
            if (loan.ReturnedNote == null && (memberFilter == null || loan.Member == memberFilter))
            {
                outIds.Add(loan.ToolId);
            }
        }
        return outIds;
    }

    /// <summary>One notice-board line per tool; unknown ids get a shrug line, not a crash.</summary>
    public string BoardLine(string toolId)
    {
        var tool = Find(toolId);
        if (tool == null)
        {
            return "unknown tool " + toolId;
        }
        var loan = ActiveLoan(toolId);
        if (loan == null)
        {
            return $"{tool.Id} {tool.Name} — in shed";
        }
        return $"{tool.Id} {tool.Name} — out to {loan.Member} (due day {loan.DayDue})";
    }

    /// <summary>Lower-cased category tag for the shelf labeller.</summary>
    public string CategorySlug(string toolId)
    {
        var tool = Find(toolId);
        if (tool == null)
        {
            throw new KeyNotFoundException("unknown tool: " + toolId);
        }
        return tool.Category.Trim().ToLowerInvariant();
    }
}
