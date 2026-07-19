# Pydantic v2 model contract migration (protected local copy)

The checked-in `pydantic_v2.py` module is the offline framework boundary for
this service migration.

- Inner `Config`, `@validator`, `@root_validator`, `json_encoders`, `dict()`,
  and `json()` are old integration APIs in this fixture.
- Configuration uses `model_config = ConfigDict(...)`. Field hooks use
  `@field_validator` with an explicit `mode`; aggregate checks use
  `@model_validator`; wire conversion uses `@field_serializer`.
- Validation entry points are `model_validate` and `model_validate_json`.
  Serialization entry points are `model_dump` and `model_dump_json`.
- This contract is strict: integers and booleans are not coerced from strings,
  and booleans are not accepted as integers.
- Input accepts documented aliases and, because `populate_by_name` is enabled,
  Python field names. Missing/type error locations use the public alias;
  aggregate invariant errors remain at the model location `()`.
- JSON output uses declaration order, aliases when requested, compact
  separators, and field serializers. Extra input is forbidden.
