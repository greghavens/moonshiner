import { MeterHub } from './hub.ts';
import type { Reading } from './hub.ts';

/**
 * Per-meter consumption aggregates for the dashboard. One instance per home;
 * attach() wires it to the daemon's hub, after which every published reading
 * and dropout should land in the aggregates.
 */
export class EnergyStats {
  private totalWatts = new Map<string, number>();
  private samples = new Map<string, number>();
  private dropouts: string[] = [];

  attach(hub: MeterHub): void {
    hub.subscribe('reading', this.onReading);
    hub.subscribe('dropout', this.onDropout);
  }

  onReading(reading: Reading): void {
    const prev = this.totalWatts.get(reading.meterId) ?? 0;
    this.totalWatts.set(reading.meterId, prev + reading.watts);
    this.samples.set(reading.meterId, (this.samples.get(reading.meterId) ?? 0) + 1);
  }

  onDropout(reading: Reading): void {
    this.dropouts.push(reading.meterId);
  }

  totalFor(meterId: string): number {
    return this.totalWatts.get(meterId) ?? 0;
  }

  averageFor(meterId: string): number {
    const n = this.samples.get(meterId) ?? 0;
    if (n === 0) return 0;
    return this.totalFor(meterId) / n;
  }

  sampleCount(meterId: string): number {
    return this.samples.get(meterId) ?? 0;
  }

  metersSeen(): string[] {
    return [...this.samples.keys()].sort();
  }

  dropoutLog(): string[] {
    return [...this.dropouts];
  }
}
