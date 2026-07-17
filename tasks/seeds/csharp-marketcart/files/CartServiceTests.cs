namespace MarketCart;

public class BasketBasicsTests
{
    [Fact]
    public void OpenedBasketStartsEmpty()
    {
        var svc = new CartService();
        var cart = svc.Open("sat-anna");
        Assert.Empty(cart.Lines);
        Assert.Equal(0m, cart.Total());
    }

    [Fact]
    public void AddingTheSameProductMergesQuantities()
    {
        var svc = new CartService();
        svc.Open("sat-anna");
        svc.AddItem("sat-anna", "eggs-dozen", 1, 6.50m);
        svc.AddItem("sat-anna", "eggs-dozen", 2, 6.50m);

        var line = Assert.Single(svc.Get("sat-anna").Lines);
        Assert.Equal(3, line.Qty);
    }

    [Fact]
    public void TotalSumsAllLines()
    {
        var svc = new CartService();
        svc.Open("sat-anna");
        svc.AddItem("sat-anna", "eggs-dozen", 2, 6.50m);
        svc.AddItem("sat-anna", "sourdough", 1, 8.00m);

        Assert.Equal(21.00m, svc.Get("sat-anna").Total());
    }
}

public class ReorderTests
{
    private static CartService SeedSavedBasket()
    {
        var svc = new CartService();
        svc.Open("sat-anna");
        svc.AddItem("sat-anna", "eggs-dozen", 2, 6.50m);
        svc.AddItem("sat-anna", "sourdough", 1, 8.00m);
        return svc;
    }

    [Fact]
    public void AddingToTheReorderLeavesTheSavedBasketAlone()
    {
        var svc = SeedSavedBasket();
        svc.Duplicate("sat-anna", "sat-anna-2");

        svc.AddItem("sat-anna-2", "honey-jar", 2, 9.25m);

        Assert.Equal(3, svc.Get("sat-anna-2").Lines.Count);
        Assert.Equal(2, svc.Get("sat-anna").Lines.Count);
        Assert.DoesNotContain(svc.Get("sat-anna").Lines, l => l.Product == "honey-jar");
    }

    [Fact]
    public void ChangingQuantitiesInTheReorderLeavesTheSavedBasketAlone()
    {
        var svc = SeedSavedBasket();
        svc.Duplicate("sat-anna", "sat-anna-2");

        svc.AddItem("sat-anna-2", "eggs-dozen", 3, 6.50m);

        Assert.Equal(5, svc.Get("sat-anna-2").Lines.Single(l => l.Product == "eggs-dozen").Qty);
        Assert.Equal(2, svc.Get("sat-anna").Lines.Single(l => l.Product == "eggs-dozen").Qty);
    }

    [Fact]
    public void RemovingFromTheReorderLeavesTheSavedBasketAlone()
    {
        var svc = SeedSavedBasket();
        svc.Duplicate("sat-anna", "sat-anna-2");

        Assert.True(svc.RemoveItem("sat-anna-2", "sourdough"));

        Assert.Single(svc.Get("sat-anna-2").Lines);
        Assert.Contains(svc.Get("sat-anna").Lines, l => l.Product == "sourdough");
    }
}

public class CheckoutHistoryTests
{
    [Fact]
    public void CheckoutKeepsTheOrderedLinesInHistory()
    {
        var svc = new CartService();
        svc.Open("sat-anna");
        svc.AddItem("sat-anna", "eggs-dozen", 2, 6.50m);
        svc.AddItem("sat-anna", "sourdough", 1, 8.00m);

        var order = svc.Checkout("sat-anna");

        Assert.Equal("ORD-1", order.Id);
        Assert.Equal(21.00m, order.Total);
        Assert.Equal(2, order.Lines.Count);
        Assert.Empty(svc.Get("sat-anna").Lines);
    }

    [Fact]
    public void HistoryStaysFrozenWhileTheBasketIsReused()
    {
        var svc = new CartService();
        svc.Open("sat-anna");
        svc.AddItem("sat-anna", "eggs-dozen", 2, 6.50m);
        svc.Checkout("sat-anna");

        svc.AddItem("sat-anna", "flowers-bunch", 1, 12.00m);

        var first = Assert.Single(svc.Orders);
        var line = Assert.Single(first.Lines);
        Assert.Equal("eggs-dozen", line.Product);
    }

    [Fact]
    public void OrderIdsCountUp()
    {
        var svc = new CartService();
        svc.Open("a");
        svc.AddItem("a", "eggs-dozen", 1, 6.50m);
        svc.Checkout("a");
        svc.AddItem("a", "sourdough", 1, 8.00m);

        Assert.Equal("ORD-2", svc.Checkout("a").Id);
    }

    [Fact]
    public void EmptyBasketCannotCheckOut()
    {
        var svc = new CartService();
        svc.Open("a");
        var ex = Assert.Throws<InvalidOperationException>(() => svc.Checkout("a"));
        Assert.Equal("cart 'a' is empty", ex.Message);
    }
}
