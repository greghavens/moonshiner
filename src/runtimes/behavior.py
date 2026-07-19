"""Normalizer for Moonshiner's OpenAI-compatible behavioral traces."""
import json

class BehaviorRuntime:
    trace_formats=("moonshiner-behavior-openai-v1",)
    @staticmethod
    def parse_stream(path, workspace=None):
        messages=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        turns=[]; initial_user_skipped=False
        for message in messages:
            if message.get("role") == "system": continue
            if message.get("role") == "user" and not initial_user_skipped:
                initial_user_skipped=True; continue
            turns.append(message)
        return turns, {}
    @staticmethod
    def tool_schemas(messages):
        names={c["function"]["name"] for m in messages for c in m.get("tool_calls",[])}
        return [{"type":"function","function":{"name":n,"description":"Fictional simulator tool",
                "parameters":{"type":"object","additionalProperties":True}}} for n in sorted(names)]
