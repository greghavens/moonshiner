namespace ToolLend;

public class LendingShedTests
{
    private static LendingShed Stocked()
    {
        var shed = new LendingShed();
        shed.AddTool(new Tool { Id = "T1", Name = "Post-hole digger", Category = "Garden" });
        shed.AddTool(new Tool { Id = "T2", Name = "Belt sander", Category = "Woodwork", Notes = "spare belts in the tin" });
        shed.AddTool(new Tool { Id = "T3", Name = "Tile cutter", Category = "Masonry" });
        return shed;
    }

    [Fact]
    public void FindKnownToolCarriesItsFields()
    {
        var shed = Stocked();
        var tool = shed.Find("T2");
        Assert.NotNull(tool);
        Assert.Equal("Belt sander", tool.Name);
        Assert.Equal("Woodwork", tool.Category);
        Assert.Equal("spare belts in the tin", tool.Notes);
    }

    [Fact]
    public void NotesAreOptionalOnDonatedTools()
    {
        var shed = Stocked();
        var tool = shed.Find("T1");
        Assert.NotNull(tool);
        Assert.Null(tool.Notes);
    }

    [Fact]
    public void FindUnknownToolComesBackEmptyHanded()
    {
        Assert.Null(Stocked().Find("T9"));
    }

    [Fact]
    public void NoReturnNoteBeforeTheFirstReturn()
    {
        Assert.Null(Stocked().LastReturnNote);
    }

    [Fact]
    public void CheckoutAndBoardLines()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        Assert.True(shed.IsOut("T1"));
        Assert.False(shed.IsOut("T2"));
        Assert.Equal("T1 Post-hole digger — out to Priya (due day 12)", shed.BoardLine("T1"));
        Assert.Equal("T2 Belt sander — in shed", shed.BoardLine("T2"));
        Assert.Equal("unknown tool T9", shed.BoardLine("T9"));
    }

    [Fact]
    public void CheckoutUnknownToolIsAHardError()
    {
        var ex = Assert.Throws<KeyNotFoundException>(() => Stocked().Checkout("T9", "Priya", 5));
        Assert.Equal("unknown tool: T9", ex.Message);
    }

    [Fact]
    public void DoubleCheckoutIsAHardError()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        var ex = Assert.Throws<InvalidOperationException>(() => shed.Checkout("T1", "Marco", 20));
        Assert.Equal("already out: T1", ex.Message);
    }

    [Fact]
    public void ReturnsComputeTheDueDateNote()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        Assert.Equal("on time", shed.Return("T1", 12));
        shed.Checkout("T2", "Marco", 15);
        Assert.Equal("3 days late", shed.Return("T2", 18));
        Assert.Equal("3 days late", shed.LastReturnNote);
        Assert.False(shed.IsOut("T1"));
    }

    [Fact]
    public void ReturningAToolThatIsNotOutIsAHardError()
    {
        var ex = Assert.Throws<InvalidOperationException>(() => Stocked().Return("T3", 4));
        Assert.Equal("not out: T3", ex.Message);
    }

    [Fact]
    public void ReturnedToolCanGoOutAgain()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        shed.Return("T1", 10);
        shed.Checkout("T1", "Marco", 25);
        Assert.Equal("T1 Post-hole digger — out to Marco (due day 25)", shed.BoardLine("T1"));
    }

    [Fact]
    public void OutListWithNoFilterShowsEveryOpenLoan()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        shed.Checkout("T2", "Marco", 15);
        shed.Checkout("T3", "Priya", 15);
        shed.Return("T2", 14);
        Assert.Equal(new List<string> { "T1", "T3" }, shed.OutList(null));
    }

    [Fact]
    public void OutListFiltersByMember()
    {
        var shed = Stocked();
        shed.Checkout("T1", "Priya", 12);
        shed.Checkout("T2", "Marco", 15);
        shed.Checkout("T3", "Priya", 15);
        Assert.Equal(new List<string> { "T1", "T3" }, shed.OutList("Priya"));
        Assert.Equal(new List<string> { "T2" }, shed.OutList("Marco"));
        Assert.Empty(shed.OutList("Nobody"));
    }

    [Fact]
    public void CategorySlugsFeedTheShelfLabeller()
    {
        var shed = Stocked();
        Assert.Equal("garden", shed.CategorySlug("T1"));
        Assert.Equal("masonry", shed.CategorySlug("T3"));
        var ex = Assert.Throws<KeyNotFoundException>(() => shed.CategorySlug("T9"));
        Assert.Equal("unknown tool: T9", ex.Message);
    }
}
