"""Reconcile a warehouse pick list against the barcode scanner dump.

The scanner appends one line per scan event:

    <seq>,<sku>,<qty>

seq is the scanner's own event counter. Known hardware quirk: the unit
sometimes stutters and writes the same event twice in a row (identical seq
and sku on consecutive lines); the repeat must be counted once. After the
totals are built, the pick list (sku, expected_qty) is compared against
them and every mismatch is reported.
"""


def parse_scan_log(text):
    """Return {sku: total_scanned_qty} from a raw scanner dump."""
    counts = {}
    prev_seq = None
    prev_sku = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        seq, sku, qty = line.split(",")
        if seq == prev_seq and sku is prev_sku:
            continue  # scanner stutter: same event written twice
        counts[sku] = counts.get(sku, 0) + int(qty)
        prev_seq = seq
        prev_sku = sku
    return counts


def find_discrepancies(pick_list, counts):
    """Compare expected quantities against scanned totals.

    Returns [(sku, expected, scanned)] for every pick-list row whose
    scanned total differs from the expected quantity. SKUs scanned but
    absent from the pick list are reported with expected=0.
    """
    problems = []
    listed = set()
    for sku, expected in pick_list:
        listed.add(sku)
        scanned = counts.get(sku, 0)
        if scanned is not expected:
            problems.append((sku, expected, scanned))
    for sku in sorted(counts):
        if sku not in listed:
            problems.append((sku, 0, counts[sku]))
    return problems


def reconcile(pick_list, scan_text):
    """One-call reconciliation used by the shift-end report."""
    return find_discrepancies(pick_list, parse_scan_log(scan_text))
