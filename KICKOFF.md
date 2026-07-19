# KICKOFF — operate Moonshiner safely

This file is retained for agents and older integrations. The CLI now owns orchestration; do not reproduce the pipeline manually.

1. Diagnose the configured runtimes:

   ```bash
   python3 moonshiner.py doctor
   ```

2. If configuration is requested, use `config role`; if a keyed provider needs a credential, use `auth set`. Never place a credential in argv, logs, chat, or repository files.

3. Dry-run the exact bounded trace command:

   ```bash
   python3 moonshiner.py run --dry-run
   ```

4. A bare run is a one-seed smoke run:

   ```bash
   python3 moonshiner.py run
   ```

5. For a larger user-authorized run, retain explicit ceilings:

   ```bash
   python3 moonshiner.py run --limit 20 --max-attempts 2 --max-calls 80 --yes
   ```

6. Inspect durable state instead of grepping human logs:

   ```bash
   python3 moonshiner.py status
   python3 moonshiner.py inspect <run-id>
   ```

7. Build accepted data separately:

   ```bash
   python3 moonshiner.py dataset build
   ```

Never infer permission to use `--all`, `--yes`, increase attempts, or increase a model-call ceiling. Never delete or replace anything under `tasks/seeds/`. See `skills/moonshiner-runner/SKILL.md` for the bundled agent workflow.
