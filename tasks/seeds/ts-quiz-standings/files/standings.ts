export type TeamResult = {
  team: string;
  points: number;
  finishedAt: number; // epoch ms when the team handed in its final answer
};

/**
 * Weekly pub-quiz league standings. More points ranks higher; when two teams
 * tie on points, the one that handed in earlier takes the better spot.
 */
export function rankTeams(results: TeamResult[]): TeamResult[] {
  const ranked = [...results];
  ranked.sort((a, b) => {
    if (a.points !== b.points) return b.points > a.points;
    return a.finishedAt > b.finishedAt;
  });
  return ranked;
}

/** Names of the top three teams, best first. */
export function podium(results: TeamResult[]): string[] {
  return rankTeams(results)
    .slice(0, 3)
    .map((r) => r.team);
}

/** 1-based position of a team in the standings, or -1 if absent. */
export function positionOf(results: TeamResult[], team: string): number {
  const idx = rankTeams(results).findIndex((r) => r.team === team);
  return idx === -1 ? -1 : idx + 1;
}

/**
 * Render the standings board as printable lines, e.g. " 1. Quizzly Bears  52".
 */
export function formatBoard(results: TeamResult[]): string[] {
  const ranked = rankTeams(results);
  const width = Math.max(0, ...ranked.map((r) => r.team.length));
  return ranked.map((r, i) => {
    const pos = String(i + 1).padStart(2, ' ');
    return `${pos}. ${r.team.padEnd(width, ' ')}  ${r.points}`;
  });
}
