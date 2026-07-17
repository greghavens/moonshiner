//! Machine totals as flat CSV for the plant historian import.

use crate::model::Report;
use crate::plugin::WriterPlugin;

pub struct MachineCsv;

impl WriterPlugin for MachineCsv {
    fn name(&self) -> &'static str {
        "csv"
    }

    fn filename(&self, _report: &Report) -> String {
        "machine_totals.csv".to_string()
    }

    fn render(&self, report: &Report) -> String {
        let mut out = String::from("machine,produced,downtime_min,scrap\n");
        for (machine, totals) in &report.machines {
            out.push_str(&format!(
                "{},{},{},{}\n",
                machine, totals.produced, totals.downtime_min, totals.scrap
            ));
        }
        out
    }
}
