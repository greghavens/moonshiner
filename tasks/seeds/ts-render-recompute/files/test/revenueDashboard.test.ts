import assert from "node:assert/strict";
import test from "node:test";

import {
  RevenueDashboard,
  type Currency,
  type DashboardProps,
  type RevenueRecord,
} from "../src/revenueDashboard.ts";

const initialRows: readonly RevenueRecord[] = [
  { id: "acme", account: "Acme", revenue: 120 },
  { id: "birch", account: "Birch", revenue: 45 },
  { id: "cobalt", account: "Cobalt", revenue: 80 },
];

function props(
  rows: readonly RevenueRecord[],
  options: {
    stale?: boolean;
    receivedAt?: string;
    minimumRevenue?: number;
    currency?: Currency;
    selectedAccountId?: string | null;
  } = {},
): DashboardProps {
  return {
    snapshot: {
      rows,
      stale: options.stale ?? false,
      receivedAt: options.receivedAt ?? "2026-01-10T12:00:00Z",
    },
    minimumRevenue: options.minimumRevenue ?? 50,
    currency: options.currency ?? "USD",
    selectedAccountId: options.selectedAccountId ?? null,
  };
}

test("parent-only updates reuse the table and reduce child renders", () => {
  const dashboard = new RevenueDashboard();

  const first = dashboard.render(props(initialRows));
  const selected = dashboard.render(
    props(initialRows, {
      receivedAt: "2026-01-10T12:01:00Z",
      selectedAccountId: "cobalt",
    }),
  );
  const unselected = dashboard.render(
    props(initialRows, {
      receivedAt: "2026-01-10T12:02:00Z",
      selectedAccountId: null,
    }),
  );

  assert.equal(selected.table, first.table);
  assert.equal(unselected.table, first.table);
  assert.equal(selected.receivedAt, "2026-01-10T12:01:00Z");
  assert.equal(selected.selectedAccountId, "cobalt");
  assert.deepEqual(dashboard.getRenderProfile(), {
    dashboardRenders: 3,
    tableRenders: 1,
    derivedStateRecomputations: 1,
  });
});

test("each table dependency invalidates the cached derived state", () => {
  const dashboard = new RevenueDashboard();

  const initial = dashboard.render(props(initialRows));
  assert.deepEqual(
    initial.table.rows.map((row) => row.id),
    ["acme", "cobalt"],
  );

  const equivalentRows: readonly RevenueRecord[] = [...initialRows];
  const equivalentReplacement = dashboard.render(props(equivalentRows));
  assert.notEqual(equivalentReplacement.table, initial.table);
  assert.deepEqual(
    equivalentReplacement.table.rows.map((row) => row.id),
    ["acme", "cobalt"],
  );

  const higherMinimum = dashboard.render(
    props(equivalentRows, { minimumRevenue: 100 }),
  );
  assert.deepEqual(
    higherMinimum.table.rows.map((row) => row.id),
    ["acme"],
  );

  const euros = dashboard.render(
    props(equivalentRows, { minimumRevenue: 100, currency: "EUR" }),
  );
  assert.equal(euros.table.rows[0]?.formattedRevenue, "EUR 120.00");

  const replacementRows: readonly RevenueRecord[] = [
    ...initialRows,
    { id: "delta", account: "Delta", revenue: 200 },
  ];
  const replaced = dashboard.render(
    props(replacementRows, { minimumRevenue: 100, currency: "EUR" }),
  );
  assert.deepEqual(
    replaced.table.rows.map((row) => row.id),
    ["delta", "acme"],
  );

  assert.deepEqual(dashboard.getRenderProfile(), {
    dashboardRenders: 5,
    tableRenders: 5,
    derivedStateRecomputations: 5,
  });
});

test("stale snapshots retain the last fresh table without poisoning dependencies", () => {
  const dashboard = new RevenueDashboard();
  const fresh = dashboard.render(props(initialRows));

  const pendingRows: readonly RevenueRecord[] = [
    { id: "delta", account: "Delta", revenue: 210 },
    { id: "elm", account: "Elm", revenue: 160 },
  ];
  const stale = dashboard.render(
    props(pendingRows, {
      stale: true,
      receivedAt: "2026-01-10T12:05:00Z",
      minimumRevenue: 200,
      currency: "EUR",
      selectedAccountId: "delta",
    }),
  );

  assert.equal(stale.stale, true);
  assert.equal(stale.receivedAt, "2026-01-10T12:05:00Z");
  assert.equal(stale.selectedAccountId, "delta");
  assert.equal(stale.table, fresh.table);
  assert.deepEqual(dashboard.getRenderProfile(), {
    dashboardRenders: 2,
    tableRenders: 1,
    derivedStateRecomputations: 1,
  });

  const refreshed = dashboard.render(
    props(pendingRows, {
      stale: false,
      receivedAt: "2026-01-10T12:06:00Z",
      minimumRevenue: 200,
      currency: "EUR",
      selectedAccountId: "delta",
    }),
  );

  assert.notEqual(refreshed.table, fresh.table);
  assert.deepEqual(
    refreshed.table.rows.map((row) => row.id),
    ["delta"],
  );
  assert.equal(refreshed.table.currency, "EUR");
  assert.equal(refreshed.table.rows[0]?.formattedRevenue, "EUR 210.00");
  assert.deepEqual(dashboard.getRenderProfile(), {
    dashboardRenders: 3,
    tableRenders: 2,
    derivedStateRecomputations: 2,
  });
});
