export function fixtureProps(value: any): { label: string } {
  console.log('fixture props', value);
  return { label: String(value) };
}
