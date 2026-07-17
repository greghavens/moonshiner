namespace MuseumLoan;

public class MuseumLoanTests
{
    private static LoanDesk SampleDesk()
    {
        var desk = new LoanDesk();
        desk.Accession(new Artifact
        {
            Id = "A1",
            Title = "Brass Astrolabe",
            Curator = new Curator { Name = "Ines Vo", Email = "  ines@stmuseum.example  " },
        });
        desk.Accession(new Artifact
        {
            Id = "A2",
            Title = "Whale Oil Lamp",
            Curator = new Curator { Name = "Tomas Reyes" },
        });
        desk.Accession(new Artifact
        {
            Id = "A3",
            Title = "Signal Lantern",
        });
        desk.AssignShelf("A1", "b-12");
        return desk;
    }

    [Fact]
    public void TitleFor_known_artifact_is_catalogue_cased()
    {
        Assert.Equal("BRASS ASTROLABE", SampleDesk().TitleFor("A1"));
    }

    [Fact]
    public void TitleFor_unknown_artifact_reads_unlisted()
    {
        Assert.Equal("(unlisted)", SampleDesk().TitleFor("A9"));
    }

    [Fact]
    public void CuratorContact_trims_the_stored_email()
    {
        Assert.Equal("ines@stmuseum.example", SampleDesk().CuratorContact("A1"));
    }

    [Fact]
    public void CuratorContact_without_email_reads_unassigned()
    {
        Assert.Equal("unassigned", SampleDesk().CuratorContact("A2"));
    }

    [Fact]
    public void CuratorContact_without_curator_reads_unassigned()
    {
        Assert.Equal("unassigned", SampleDesk().CuratorContact("A3"));
    }

    [Fact]
    public void CuratorContact_unknown_artifact_says_so()
    {
        Assert.Equal("unknown artifact", SampleDesk().CuratorContact("A9"));
    }

    [Fact]
    public void ShelfFor_prints_upper_case_pull_slip_codes()
    {
        Assert.Equal("B-12", SampleDesk().ShelfFor("A1"));
    }

    [Fact]
    public void ShelfFor_unshelved_artifact_reads_unassigned()
    {
        Assert.Equal("unassigned", SampleDesk().ShelfFor("A2"));
    }

    [Fact]
    public void CheckOut_records_an_open_loan()
    {
        var desk = SampleDesk();
        var receipt = desk.CheckOut("A1", "Mara Okafor");
        Assert.Equal("A1 -> MARA OKAFOR", receipt.Summary());
        Assert.Equal(1, desk.OpenLoans);
    }

    [Fact]
    public void CheckOut_requires_a_borrower_name()
    {
        var desk = SampleDesk();
        Assert.Throws<ArgumentException>(() => desk.CheckOut("A1", null));
        Assert.Equal(0, desk.OpenLoans);
    }

    [Fact]
    public void CheckOut_blank_borrower_is_rejected_too()
    {
        var desk = SampleDesk();
        Assert.Throws<ArgumentException>(() => desk.CheckOut("A1", "   "));
        Assert.Equal(0, desk.OpenLoans);
    }

    [Fact]
    public void CheckOut_unknown_artifact_is_rejected()
    {
        Assert.Throws<ArgumentException>(() => SampleDesk().CheckOut("A9", "Mara Okafor"));
    }

    [Fact]
    public void Accession_rejects_duplicate_ids()
    {
        var desk = SampleDesk();
        Assert.Throws<ArgumentException>(() => desk.Accession(new Artifact { Id = "A1", Title = "Copy" }));
    }

    [Fact]
    public void Find_returns_null_for_unknown_ids()
    {
        Assert.Null(SampleDesk().Find("A9"));
    }
}
