export interface Plot {
  id: string;
  bed: string; // e.g. "A3" — row letter + stake number
  sqft: number;
  raised: boolean;
}

export const ZONES = ["north", "south", "creek"] as const;
export type Zone = (typeof ZONES)[number];

// Rows A/B sit along the north fence, C/D in the middle field, E/F back
// onto the creek. The watering rota is organized by these zones.
export function zoneOf(bed: string): Zone {
  const row = bed.charAt(0).toUpperCase();
  if (row === "A" || row === "B") {
    return "north";
  }
  if (row === "C" || row === "D") {
    return "south";
  }
  if (row === "E" || row === "F") {
    return "creek";
  }
  throw new Error(`unknown bed row: ${bed}`);
}
