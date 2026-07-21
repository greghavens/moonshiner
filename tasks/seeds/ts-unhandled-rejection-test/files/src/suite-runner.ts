export interface SuiteCase {
  name: string;
  run: () => void | Promise<void>;
}

export interface CaseResult {
  name: string;
  status: "passed" | "failed";
  error?: string;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function nextEventLoopTurn(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

export async function runSuite(cases: readonly SuiteCase[]): Promise<CaseResult[]> {
  const results: CaseResult[] = [];
  let activeResult: CaseResult | undefined;

  const recordUnhandledRejection = (reason: unknown): void => {
    if (activeResult) {
      activeResult.status = "failed";
      activeResult.error = messageFrom(reason);
    }
  };

  process.on("unhandledRejection", recordUnhandledRejection);

  try {
    for (const suiteCase of cases) {
      const result: CaseResult = { name: suiteCase.name, status: "passed" };
      activeResult = result;
      results.push(result);

      try {
        suiteCase.run();
      } catch (error) {
        result.status = "failed";
        result.error = messageFrom(error);
      }
    }

    // Give Node a chance to report promises that a case failed to observe.
    await nextEventLoopTurn();
    return results;
  } finally {
    process.off("unhandledRejection", recordUnhandledRejection);
  }
}
