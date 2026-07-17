// Quarterly vendor-spend report. Rendered server-side for the email digest
// and the dashboard snapshot; data arrives as parallel arrays from the
// planning sheet importer (vendors[i] lines up with budgets[i]).

import { emphasize, escapeHtml, money, vendorLabel } from "./format.ts";

export interface Vendor {
  name: string;
  spend: number;
}

export interface ReportProps {
  quarter: string;
  vendors: Vendor[];
  budgets: number[];
  footnote: string;
}

function SummaryCards({ vendors, budgets }: { vendors: Vendor[]; budgets: number[] }) {
  const total = vendors.reduce((sum, v) => sum + v.spend, 0);
  const overBudget = vendors.filter((v, i) => v.spend > budgets[i]).length;
  return (
    <ul className="summary">
      <li>Total spend: {money(total)}</li>
      <li>Vendors: {vendors.length}</li>
      <li>Over budget: {overBudget}</li>
    </ul>
  );
}

function VarianceList({ vendors, budgets }: { vendors: Vendor[]; budgets: number[] }) {
  return (
    <ul className="variance">
      {vendors.map((v, i) => {
        const delta = v.spend - budgets[i];
        const note =
          delta > 0 ? `over by ${money(delta)}` : delta < 0 ? `under by ${money(-delta)}` : "on budget";
        return (
          <li key={v.name}>
            {vendorLabel(v.name)}: {note}
          </li>
        );
      })}
    </ul>
  );
}

function TopVendorsTable({ vendors }: { vendors: Vendor[] }) {
  const ranked = vendors.sort((a, b) => b.spend - a.spend).slice(0, 3);
  return (
    <table className="top-vendors">
      <caption>Top 3 by spend</caption>
      <tbody>
        {ranked.map((v) => (
          <tr key={v.name}>
            <td>{vendorLabel(v.name)}</td>
            <td>{money(v.spend)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function QuarterlyReport({ quarter, vendors, budgets, footnote }: ReportProps) {
  return (
    <section className="report">
      <h1>Vendor spend, {quarter}</h1>
      <SummaryCards vendors={vendors} budgets={budgets} />
      <VarianceList vendors={vendors} budgets={budgets} />
      <TopVendorsTable vendors={vendors} />
      <p className="footnote" dangerouslySetInnerHTML={{ __html: emphasize(escapeHtml(footnote)) }} />
    </section>
  );
}
