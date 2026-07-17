namespace WordSearch.Tests;

public class LcgTests
{
    [Fact]
    public void NextFollowsThePinnedRecurrence()
    {
        var rng = new Lcg(1);
        Assert.Equal(1103527590, rng.Next());
        Assert.Equal(377401575, rng.Next());
        Assert.Equal(662824084, rng.Next());
    }

    [Fact]
    public void NextFromSeedFortyTwo()
    {
        var rng = new Lcg(42);
        Assert.Equal(1250496027, rng.Next());
        Assert.Equal(1116302264, rng.Next());
        Assert.Equal(1000676753, rng.Next());
    }

    [Fact]
    public void RandReducesModuloK()
    {
        var rng = new Lcg(1);
        Assert.Equal(0, rng.Rand(10));
        Assert.Equal(5, rng.Rand(10));
        Assert.Equal(4, rng.Rand(10));
    }

    [Fact]
    public void RejectsNegativeSeed()
    {
        Assert.Throws<ArgumentException>(() => new Lcg(-1));
    }

    [Fact]
    public void RejectsNonPositiveModulus()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new Lcg(1).Rand(0));
    }
}

public class GeneratorTests
{
    private static readonly string[] Tools = { "HAMMER", "CHISEL", "WRENCH", "PLIERS", "SAW", "DRILL" };
    private static readonly string[] Flowers = { "TULIP", "DAFFODIL", "ROSE", "PEONY", "IRIS" };

    [Fact]
    public void SeedFortyTwoGridIsPinned()
    {
        Assert.Equal(Fixtures.Grid("seed42.grid"), Generator.Generate(10, 10, Tools, 42));
    }

    [Fact]
    public void SeedSevenGridIsPinned()
    {
        Assert.Equal(Fixtures.Grid("seed7.grid"), Generator.Generate(8, 12, Flowers, 7));
    }

    [Fact]
    public void OverlappingPlacementsShareCells()
    {
        // 9 letters into 5 cells: HELL must land on top of HELLO
        Assert.Equal("OLLEH", Generator.Generate(1, 5, new[] { "HELLO", "HELL" }, 1));
    }

    [Fact]
    public void InputWordsAreUppercased()
    {
        Assert.Equal("OLLEH", Generator.Generate(1, 5, new[] { "hello", "Hell" }, 1));
    }

    [Fact]
    public void RejectsDuplicateWords()
    {
        Assert.Throws<ArgumentException>(
            () => Generator.Generate(5, 5, new[] { "SAW", "saw" }, 1));
    }

    [Fact]
    public void RejectsShortOrNonAlphabeticWords()
    {
        Assert.Throws<ArgumentException>(() => Generator.Generate(5, 5, new[] { "A" }, 1));
        Assert.Throws<ArgumentException>(() => Generator.Generate(5, 5, new[] { "SAW!" }, 1));
    }

    [Fact]
    public void ThrowsWhenAWordCannotBePlaced()
    {
        Assert.Throws<InvalidOperationException>(
            () => Generator.Generate(1, 4, new[] { "AAAA", "BBBB" }, 1));
        Assert.Throws<InvalidOperationException>(
            () => Generator.Generate(3, 3, new[] { "LONGWORD" }, 1));
    }
}

public class SolverTests
{
    [Fact]
    public void FindsPalindromesInBothDirections()
    {
        var grid = string.Join('\n', "NOON", "OXOX", "OXOX", "NOON");
        Assert.Equal(new[]
        {
            "NOON 0 0 E",
            "NOON 0 0 S",
            "NOON 0 3 W",
            "NOON 3 0 E",
            "NOON 3 0 N",
            "NOON 3 3 W",
        }, Solver.Find(grid, new[] { "NOON" }).Select(p => p.ToString()));
    }

    [Fact]
    public void FindsEveryToolInTheSeedFortyTwoGrid()
    {
        // query order deliberately scrambled: results are sorted by word, row, col, direction
        var found = Solver.Find(Fixtures.Grid("seed42.grid"),
                                new[] { "SAW", "WRENCH", "CHISEL", "DRILL", "PLIERS", "HAMMER" });
        Assert.Equal(new[]
        {
            "CHISEL 6 9 NW",
            "DRILL 7 8 W",
            "HAMMER 5 2 N",
            "PLIERS 8 1 NE",
            "SAW 5 0 S",
            "WRENCH 0 7 SW",
        }, found.Select(p => p.ToString()));
    }

    [Fact]
    public void FindsEveryFlowerInTheSeedSevenGrid()
    {
        var found = Solver.Find(Fixtures.Grid("seed7.grid"),
                                new[] { "ROSE", "IRIS", "TULIP", "PEONY", "DAFFODIL" });
        Assert.Equal(new[]
        {
            "DAFFODIL 7 0 N",
            "IRIS 7 4 N",
            "PEONY 0 9 SW",
            "ROSE 1 2 E",
            "TULIP 5 10 W",
        }, found.Select(p => p.ToString()));
    }

    [Fact]
    public void PlacementExposesStructuredFields()
    {
        var p = Solver.Find("CAT\nXXX\nXXX", new[] { "CAT" }).Single();
        Assert.Equal("CAT", p.Word);
        Assert.Equal(0, p.Row);
        Assert.Equal(0, p.Col);
        Assert.Equal("E", p.Direction);
    }

    [Fact]
    public void AbsentWordsSimplyDoNotAppear()
    {
        var found = Solver.Find(Fixtures.Grid("seed42.grid"), new[] { "QQQ", "SAW" });
        Assert.Equal(new[] { "SAW 5 0 S" }, found.Select(p => p.ToString()));
    }

    [Fact]
    public void QueryWordsAreUppercased()
    {
        var grid = string.Join('\n', "NOON", "OXOX", "OXOX", "NOON");
        Assert.Equal(6, Solver.Find(grid, new[] { "noon" }).Count);
    }

    [Fact]
    public void RejectsRaggedOrNonUppercaseGrids()
    {
        Assert.Throws<FormatException>(() => Solver.Find("ABC\nAB", new[] { "AB" }));
        Assert.Throws<FormatException>(() => Solver.Find("abc\nabc", new[] { "AB" }));
        Assert.Throws<FormatException>(() => Solver.Find("", new[] { "AB" }));
    }

    [Fact]
    public void RejectsBadQueryLists()
    {
        var grid = "CAT\nXXX\nXXX";
        Assert.Throws<ArgumentException>(() => Solver.Find(grid, new[] { "CAT", "cat" }));
        Assert.Throws<ArgumentException>(() => Solver.Find(grid, new[] { "C" }));
    }
}

internal static class Fixtures
{
    public static string Grid(string name) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "testdata", name))
            .ReplaceLineEndings("\n").TrimEnd('\n');
}
