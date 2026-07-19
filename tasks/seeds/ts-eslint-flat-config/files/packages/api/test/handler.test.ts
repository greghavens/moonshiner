export function fakeRequest(body: any): string {
  console.log('fixture request', body);
  return String(body.id);
}
