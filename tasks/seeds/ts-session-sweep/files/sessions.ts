export type Session = {
  token: string;
  userId: string;
  expiresAt: number; // epoch ms; a session expires the instant now >= expiresAt
};

export class SessionStore {
  private sessions: Session[];
  private nextId: number;

  constructor() {
    this.sessions = [];
    this.nextId = 1;
  }

  issue(userId: string, ttlMs: number, now: number): Session {
    const session = {
      token: `tok_${this.nextId++}`,
      userId,
      expiresAt: now + ttlMs,
    };
    this.sessions.push(session);
    return session;
  }

  authenticate(token: string, now: number): Session | undefined {
    return this.sessions.find((s) => s.token === token && s.expiresAt > now);
  }

  activeCount(now: number): number {
    return this.sessions.filter((s) => s.expiresAt > now).length;
  }

  /** Nightly cleanup: drop expired sessions, return how many were dropped. */
  sweep(now: number): number {
    let dropped = 0;
    this.sessions.forEach((session, index) => {
      if (session.expiresAt <= now) {
        this.sessions.splice(index, 1);
        dropped++;
      }
    });
    return dropped;
  }

  /** Kill every session belonging to a user (e.g. after a password reset). */
  revokeUser(userId: string): number {
    let revoked = 0;
    for (let i = 0; i < this.sessions.length; i++) {
      if (this.sessions[i].userId === userId) {
        this.sessions.splice(i, 1);
        revoked++;
      }
    }
    return revoked;
  }

  /** Rows currently held in memory, live or not. */
  storedCount(): number {
    return this.sessions.length;
  }
}
