export async function handleJob(id: string): Promise<string> {
  const accepted = await Promise.resolve(id.trim());
  return `accepted:${accepted}`;
}
