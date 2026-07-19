# KICKOFF — operate Moonshiner safely

This file is retained for agents and older integrations. The CLI now owns orchestration; do not reproduce the pipeline manually.

1. Diagnose the configured runtimes:

   ```bash
   moonshiner doctor
   ```

2. If configuration is requested, use `config role`; if a keyed provider needs a credential, use `auth set`. Never place a credential in argv, logs, chat, or repository files.

3. A bare invocation starts all queues enabled in the project configuration:

   ```bash
   moonshiner
   ```

4. Use a bounded dry run only when diagnosing selection:

   ```bash
   moonshiner run --dry-run
   ```

5. For a larger user-authorized run, retain explicit ceilings:

   ```bash
   moonshiner run --limit 20 --max-attempts 2 --max-calls 80 --yes
   ```

6. Inspect durable state instead of grepping human logs:

   ```bash
   moonshiner status
   moonshiner inspect <run-id>
   ```

7. Normal formatting and HF publication are automatic after acceptance.

Never infer permission to use `--all`, `--yes`, increase attempts, or increase a model-call ceiling. Never delete or replace anything under `tasks/seeds/`. See `skills/moonshiner-runner/SKILL.md` for the bundled agent workflow.
