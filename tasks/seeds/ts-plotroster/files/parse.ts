import type { Plot } from "./plot";

// Roster files come from the coordinator's spreadsheet export: one plot per
// line as "id,bed,sqft,raised", where raised is yes/no. Blank lines and
// '#' comment lines are skipped. Line numbers in errors are 1-based and
// count every line, including the skipped ones.
export function parseRoster(text: string): Plot[] {
  const plots: Plot[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const parts = line.split(",").map((p) => p.trim());
    if (parts.length !== 4) {
      throw new Error(`roster line ${i + 1}: expected 4 fields`);
    }
    const [id, bed, sqftText, raisedText] = parts;
    if (!id || !bed) {
      throw new Error(`roster line ${i + 1}: missing id or bed`);
    }
    const sqft = Number(sqftText);
    if (!Number.isFinite(sqft) || sqft <= 0) {
      throw new Error(`roster line ${i + 1}: bad sqft`);
    }
    if (raisedText !== "yes" && raisedText !== "no") {
      throw new Error(`roster line ${i + 1}: raised must be yes or no`);
    }
    plots.push({ id, bed, sqft, raised: raisedText === "yes" });
  }
  return plots;
}
