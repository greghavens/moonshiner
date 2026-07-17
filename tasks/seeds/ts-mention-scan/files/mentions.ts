const MENTION_RE = /@([a-z0-9_]{2,20})\b/g;
const USERNAME_RE = /[a-z0-9_]{2,20}/;

/** True if the message mentions anyone. */
export function hasMention(text: string): boolean {
  return MENTION_RE.test(text);
}

/** Usernames mentioned in the message, first-appearance order, no duplicates. */
export function extractMentions(text: string): string[] {
  const seen: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = MENTION_RE.exec(text)) !== null) {
    if (!seen.includes(match[1])) {
      seen.push(match[1]);
    }
  }
  return seen;
}

/** A handle is 2-20 characters: lowercase letters, digits, or underscore. */
export function isValidUsername(name: string): boolean {
  return USERNAME_RE.test(name);
}

/** Users to ping for a message: mentioned, registered, and not the author. */
export function notifyList(text: string, author: string, registered: string[]): string[] {
  if (!hasMention(text)) {
    return [];
  }
  return extractMentions(text).filter(
    (user) => user !== author && registered.includes(user),
  );
}
