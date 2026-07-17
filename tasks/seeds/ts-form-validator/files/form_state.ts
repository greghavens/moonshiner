export type Rule = {
  test: (value: string) => boolean;
  message: string;
};

export type FieldHandlers = {
  onChange: (name: string, value: string) => void;
  onSubmit: () => string[];
};

export class FormState {
  private values: Map<string, string>;
  private rules: Map<string, Rule[]>;
  private touched: Set<string>;

  constructor() {
    this.values = new Map();
    this.rules = new Map();
    this.touched = new Set();
  }

  addField(name: string, rules: Rule[] = []): void {
    this.values.set(name, "");
    this.rules.set(name, rules);
  }

  setValue(name: string, value: string): void {
    if (!this.values.has(name)) throw new Error(`unknown field: ${name}`);
    this.values.set(name, value);
    this.touched.add(name);
  }

  value(name: string): string {
    return this.values.get(name) ?? "";
  }

  isDirty(): boolean {
    return this.touched.size > 0;
  }

  validateField(name: string): string[] {
    const current = this.values.get(name) ?? "";
    const failures: string[] = [];
    for (const rule of this.rules.get(name) ?? []) {
      if (!rule.test(current)) failures.push(`${name}: ${rule.message}`);
    }
    return failures;
  }

  validateAll(): string[] {
    return [...this.values.keys()].flatMap(this.validateField);
  }

  /** Callbacks for the view layer (change events and submit). */
  handlers(): FieldHandlers {
    return { onChange: this.setValue, onSubmit: this.validateAll };
  }
}
