// tabskit — the docs site's disclosure widgets: compound Tabs and Accordion
// components wired together through context. Rendering is a pure function of
// props, so the same components serve the static build and the client.

import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  useContext,
  useState,
} from "react";
import type { ReactNode } from "react";

/** Clone direct children with their positional index injected. */
function withIndex(children: ReactNode): ReactNode {
  return Children.map(children, (child, index) =>
    isValidElement<{ index?: number }>(child) ? cloneElement(child, { index }) : child,
  );
}

// ---------------------------------------------------------------- Tabs

const TabsContext = createContext<number>(0);

interface TabsProps {
  children?: ReactNode;
  defaultIndex?: number;
}

export function Tabs({ children, defaultIndex = 0 }: TabsProps) {
  const [active] = useState(defaultIndex);
  return (
    <div className="tabs">
      <TabsContext.Provider value={active}>{children}</TabsContext.Provider>
    </div>
  );
}

interface TabListProps {
  children?: ReactNode;
  label?: string;
}

export function TabList({ children }: TabListProps) {
  return <div className="tab-list">{withIndex(children)}</div>;
}

interface TabProps {
  children?: ReactNode;
  index?: number;
}

export function Tab({ children, index = 0 }: TabProps) {
  const active = useContext(TabsContext);
  return (
    <button type="button" className={index === active ? "tab is-active" : "tab"}>
      {children}
    </button>
  );
}

export function TabPanels({ children }: { children?: ReactNode }) {
  return <div className="tab-panels">{withIndex(children)}</div>;
}

interface TabPanelProps {
  children?: ReactNode;
  index?: number;
}

export function TabPanel({ children, index = 0 }: TabPanelProps) {
  const active = useContext(TabsContext);
  return (
    <div className="tab-panel" hidden={index !== active || undefined}>
      {children}
    </div>
  );
}

// ------------------------------------------------------------ Accordion

const AccordionContext = createContext<ReadonlySet<number>>(new Set());

interface AccordionProps {
  children?: ReactNode;
  defaultOpen?: number[];
}

export function Accordion({ children, defaultOpen = [] }: AccordionProps) {
  const [open] = useState<ReadonlySet<number>>(() => new Set(defaultOpen));
  return (
    <div className="accordion">
      <AccordionContext.Provider value={open}>{withIndex(children)}</AccordionContext.Provider>
    </div>
  );
}

interface AccordionItemProps {
  title: string;
  children?: ReactNode;
  index?: number;
}

export function AccordionItem({ title, children, index = 0 }: AccordionItemProps) {
  const open = useContext(AccordionContext);
  const isOpen = open.has(index);
  return (
    <div className="acc-item">
      <h3 className="acc-header">
        <button type="button" className="acc-trigger">
          {title}
        </button>
      </h3>
      <div className="acc-panel" hidden={!isOpen || undefined}>
        {children}
      </div>
    </div>
  );
}
