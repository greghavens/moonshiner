namespace OrchardSales;

/// <summary>One variety line on a delivery ticket: how many crates and the
/// agreed per-crate price for this drop-off.</summary>
public sealed record CrateLine(string Variety, int Crates, decimal PricePerCrate);

/// <summary>A single drop-off at the co-op dock. A grower can deliver several
/// times a week from different orchard blocks; a ticket with no lines is a
/// recorded arrival that graded out entirely (happens after hail).</summary>
public sealed record Delivery(string GrowerId, string Orchard, DateOnly Day, IReadOnlyList<CrateLine> Lines);
