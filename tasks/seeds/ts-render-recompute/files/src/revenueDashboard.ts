export type Currency = "USD" | "EUR";

export interface RevenueRecord {
  readonly id: string;
  readonly account: string;
  readonly revenue: number;
}

export interface RevenueSnapshot {
  readonly rows: readonly RevenueRecord[];
  readonly stale: boolean;
  readonly receivedAt: string;
}

export interface DashboardProps {
  readonly snapshot: RevenueSnapshot;
  readonly minimumRevenue: number;
  readonly currency: Currency;
  readonly selectedAccountId: string | null;
}

export interface RevenueTableRow {
  readonly id: string;
  readonly label: string;
  readonly formattedRevenue: string;
}

export interface RevenueTableView {
  readonly rows: readonly RevenueTableRow[];
  readonly totalRevenue: number;
  readonly currency: Currency;
}

export interface DashboardView {
  readonly stale: boolean;
  readonly receivedAt: string;
  readonly selectedAccountId: string | null;
  readonly table: RevenueTableView;
}

export interface RenderProfile {
  readonly dashboardRenders: number;
  readonly tableRenders: number;
  readonly derivedStateRecomputations: number;
}

function compareRevenue(left: RevenueRecord, right: RevenueRecord): number {
  if (left.revenue !== right.revenue) {
    return right.revenue - left.revenue;
  }

  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
}

function formatRevenue(amount: number, currency: Currency): string {
  return `${currency} ${amount.toFixed(2)}`;
}

class RevenueTable {
  private renderCount = 0;
  private derivedStateRecomputationCount = 0;

  render(
    rows: readonly RevenueRecord[],
    minimumRevenue: number,
    currency: Currency,
  ): RevenueTableView {
    this.renderCount += 1;
    return this.deriveVisibleRows(rows, minimumRevenue, currency);
  }

  profile(): Pick<RenderProfile, "tableRenders" | "derivedStateRecomputations"> {
    return {
      tableRenders: this.renderCount,
      derivedStateRecomputations: this.derivedStateRecomputationCount,
    };
  }

  private deriveVisibleRows(
    rows: readonly RevenueRecord[],
    minimumRevenue: number,
    currency: Currency,
  ): RevenueTableView {
    this.derivedStateRecomputationCount += 1;

    const visibleRows = rows
      .filter((row) => row.revenue >= minimumRevenue)
      .toSorted(compareRevenue);

    return {
      rows: visibleRows.map((row) => ({
        id: row.id,
        label: row.account,
        formattedRevenue: formatRevenue(row.revenue, currency),
      })),
      totalRevenue: visibleRows.reduce((total, row) => total + row.revenue, 0),
      currency,
    };
  }
}

/**
 * A small React-style parent/child render model used by the profiling tests.
 * Snapshot row arrays are immutable inputs and therefore dependency identity is
 * significant in the same way it is in a React dependency list.
 */
export class RevenueDashboard {
  private dashboardRenderCount = 0;
  private readonly table = new RevenueTable();
  private lastFreshTableView: RevenueTableView | undefined;

  render(props: DashboardProps): DashboardView {
    this.dashboardRenderCount += 1;

    let tableView: RevenueTableView;
    if (props.snapshot.stale && this.lastFreshTableView !== undefined) {
      tableView = this.lastFreshTableView;
    } else {
      tableView = this.table.render(
        props.snapshot.rows,
        props.minimumRevenue,
        props.currency,
      );

      if (!props.snapshot.stale) {
        this.lastFreshTableView = tableView;
      }
    }

    return {
      stale: props.snapshot.stale,
      receivedAt: props.snapshot.receivedAt,
      selectedAccountId: props.selectedAccountId,
      table: tableView,
    };
  }

  getRenderProfile(): RenderProfile {
    return {
      dashboardRenders: this.dashboardRenderCount,
      ...this.table.profile(),
    };
  }
}
