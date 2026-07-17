namespace SwimHeats;

public class HeatSheetTests
{
    private static HeatSheet SampleSheet()
    {
        var sheet = new HeatSheet();
        sheet.Enter(new Entry("Rosa Lindqvist", "Dockside", 12, 31.2));
        sheet.Enter(new Entry("Amy Chen", "Ridgeline", 10, 35.5));
        sheet.Enter(new Entry("Priya Nair", "Dockside", 14, 29.8));
        sheet.Enter(new Entry("Jonah Ellis", "Harbor", 15, 27.4));
        sheet.Enter(new Entry("Sam Whitaker", "Ridgeline", 11, 33.0));
        sheet.Enter(new Entry("Tessa Boyd", "Harbor", 16, 28.9));
        sheet.Enter(new Entry("Leo Marsh", "Dockside", 9, 40.1));
        return sheet;
    }

    [Fact]
    public void TopSeedLine_names_the_fastest_entry_with_club_and_age()
    {
        Assert.Equal("Jonah Ellis (Harbor, 15)", SampleSheet().TopSeedLine());
    }

    [Fact]
    public void TopSeedLine_on_an_empty_sheet_throws()
    {
        Assert.Throws<InvalidOperationException>(() => new HeatSheet().TopSeedLine());
    }

    [Fact]
    public void JuniorCount_counts_swimmers_under_fifteen()
    {
        Assert.Equal(5, SampleSheet().JuniorCount());
    }

    [Fact]
    public void JuniorCount_is_zero_on_an_empty_sheet()
    {
        Assert.Equal(0, new HeatSheet().JuniorCount());
    }

    [Fact]
    public void Query_filters_chain()
    {
        var dockside = SampleSheet().Query().Where(e => e.Club == "Dockside");
        Assert.Equal(3, dockside.Count);
        Assert.Equal(2, dockside.Where(e => e.Age >= 12).Count);
    }

    [Fact]
    public void Query_items_materializes_the_current_filter()
    {
        var names = SampleSheet().Query().Where(e => e.Club == "Harbor").Items()
            .Select(e => e.Swimmer).ToList();
        Assert.Equal(new[] { "Jonah Ellis", "Tessa Boyd" }, names);
    }

    [Fact]
    public void Roster_is_keyed_by_printed_age_group_labels()
    {
        var roster = SampleSheet().RosterByAgeGroup();
        Assert.Equal(4, roster.Count);
        Assert.Equal(new[] { "Amy Chen", "Leo Marsh" }, roster["10&U"]);
        Assert.Equal(new[] { "Rosa Lindqvist", "Sam Whitaker" }, roster["11-12"]);
        Assert.Equal(new[] { "Priya Nair" }, roster["13-14"]);
        Assert.Equal(new[] { "Jonah Ellis", "Tessa Boyd" }, roster["15&O"]);
    }

    [Fact]
    public void Heats_seed_fastest_first_in_lane_sized_groups()
    {
        var heats = SampleSheet().Heats(4);
        Assert.Equal(2, heats.Count);
        Assert.Equal(new[] { "Jonah Ellis", "Tessa Boyd", "Priya Nair", "Rosa Lindqvist" },
            heats[0].Select(s => s.Swimmer).ToArray());
        Assert.Equal(new[] { "Sam Whitaker", "Amy Chen", "Leo Marsh" },
            heats[1].Select(s => s.Swimmer).ToArray());
        Assert.Equal(27.4, heats[0][0].Seconds);
    }

    [Fact]
    public void Heats_on_an_empty_sheet_is_an_empty_list()
    {
        Assert.Empty(new HeatSheet().Heats(4));
    }

    [Fact]
    public void Heats_requires_at_least_one_lane()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => SampleSheet().Heats(0));
    }
}
