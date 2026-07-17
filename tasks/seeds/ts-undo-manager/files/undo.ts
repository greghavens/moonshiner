// Undo/redo stack for the diagram editor. Commands carry their own apply
// and revert closures; the manager owns ordering: undo pops in LIFO order,
// redo replays, and any fresh edit invalidates the redo stack.

export interface Command {
  label: string;
  apply(): void;
  revert(): void;
}

export class UndoManager {
  private undoStack: Command[] = [];
  private redoStack: Command[] = [];

  execute(command: Command): void {
    command.apply();
    this.undoStack.push(command);
    this.redoStack = [];
  }

  undo(): boolean {
    const command = this.undoStack.pop();
    if (!command) return false;
    command.revert();
    this.redoStack.push(command);
    return true;
  }

  redo(): boolean {
    const command = this.redoStack.pop();
    if (!command) return false;
    command.apply();
    this.undoStack.push(command);
    return true;
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  /** Labels of undoable entries, oldest first. */
  historyLabels(): string[] {
    return this.undoStack.map((command) => command.label);
  }
}
