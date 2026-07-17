// Form validation core used by the settings screens. Fields hold a value
// and an ordered list of validators; a validator returns an error message
// or null. All validation here is synchronous.

export type Validator = (value: unknown) => string | null;

export function required(message = 'is required'): Validator {
  return (value) =>
    value === undefined || value === null || value === '' ? message : null;
}

export function minLength(n: number, message?: string): Validator {
  return (value) =>
    typeof value === 'string' && value.length < n
      ? message ?? `must be at least ${n} characters`
      : null;
}

export function pattern(re: RegExp, message = 'has an invalid format'): Validator {
  return (value) =>
    typeof value === 'string' && !re.test(value) ? message : null;
}

interface FieldState {
  validators: Validator[];
  value: unknown;
}

export class FormValidator {
  private fields = new Map<string, FieldState>();

  private field(name: string): FieldState {
    const state = this.fields.get(name);
    if (!state) throw new Error(`unknown field: ${name}`);
    return state;
  }

  addField(name: string, validators: Validator[] = []): void {
    if (this.fields.has(name)) throw new Error(`field already exists: ${name}`);
    this.fields.set(name, { validators, value: undefined });
  }

  setValue(name: string, value: unknown): void {
    this.field(name).value = value;
  }

  getValue(name: string): unknown {
    return this.field(name).value;
  }

  validateField(name: string): string[] {
    const state = this.field(name);
    const errors: string[] = [];
    for (const validate of state.validators) {
      const message = validate(state.value);
      if (message !== null) errors.push(message);
    }
    return errors;
  }

  validateAll(): Record<string, string[]> {
    const result: Record<string, string[]> = {};
    for (const name of this.fields.keys()) {
      result[name] = this.validateField(name);
    }
    return result;
  }

  isValid(): boolean {
    return Object.values(this.validateAll()).every((errors) => errors.length === 0);
  }
}
