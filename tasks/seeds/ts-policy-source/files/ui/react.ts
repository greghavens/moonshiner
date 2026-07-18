// Hermetic React element surface used by the fixture's UI component. The
// production bundle provides the same createElement contract from React; this
// small runtime keeps the offline acceptance suite dependency-free.
export type ReactComponent<Props> = (props: Props) => ReactElement;
export type ReactElementType = string | ReactComponent<unknown>;

export interface ReactElement {
  readonly type: ReactElementType;
  readonly props: Readonly<Record<string, unknown>>;
}

export function createElement(
  type: ReactElementType,
  props: Record<string, unknown> | null,
  ...children: unknown[]
): ReactElement {
  return {
    type,
    props: {
      ...(props ?? {}),
      children: children.length === 1 ? children[0] : children,
    },
  };
}
