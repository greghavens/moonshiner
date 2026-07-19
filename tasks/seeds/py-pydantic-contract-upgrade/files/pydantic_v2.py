"""Protected, dependency-free surface of the Pydantic v2 contract in this seed."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Mapping


class PydanticUserError(RuntimeError):
    pass


class ValidationError(ValueError):
    def __init__(self, issues: list[dict[str, object]]):
        super().__init__(f"{len(issues)} validation error(s)")
        self._issues = issues

    def errors(self) -> list[dict[str, object]]:
        return [dict(issue) for issue in self._issues]


_MISSING = object()


@dataclass(frozen=True)
class FieldInfo:
    alias: str | None = None
    default: object = _MISSING


def Field(*, alias: str | None = None, default: object = _MISSING) -> FieldInfo:
    return FieldInfo(alias=alias, default=default)


def ConfigDict(**values: object) -> dict[str, object]:
    return dict(values)


def _decorate(target, attribute: str, value: object):
    function = target.__func__ if isinstance(target, classmethod) else target
    setattr(function, attribute, value)
    return target


def validator(*fields: str, pre: bool = False):
    return lambda target: _decorate(target, "__removed_validator__", (fields, pre))


def root_validator(*, skip_on_failure: bool = False):
    return lambda target: _decorate(
        target, "__removed_root_validator__", skip_on_failure
    )


def field_validator(*fields: str, mode: str = "after"):
    if mode not in {"before", "after"}:
        raise TypeError("field_validator mode must be before or after")
    return lambda target: _decorate(target, "__field_validator__", (fields, mode))


def model_validator(*, mode: str):
    if mode != "after":
        raise TypeError("this local contract supports after model validators")
    return lambda target: _decorate(target, "__model_validator__", mode)


def field_serializer(*fields: str):
    return lambda target: _decorate(target, "__field_serializer__", fields)


class ModelMeta(type):
    def __new__(metaclass, name, bases, namespace):
        model = super().__new__(metaclass, name, bases, namespace)
        inherited_fields: OrderedDict[str, tuple[type, FieldInfo]] = OrderedDict()
        for base in bases:
            inherited_fields.update(getattr(base, "__model_fields__", {}))

        annotations = namespace.get("__annotations__", {})
        for field_name, field_type in annotations.items():
            configured = namespace.get(field_name, _MISSING)
            if isinstance(configured, FieldInfo):
                info = configured
            else:
                info = FieldInfo(default=configured)
            inherited_fields[field_name] = (field_type, info)
        model.__model_fields__ = inherited_fields

        field_validators = []
        model_validators = []
        serializers: dict[str, object] = {}
        removed: list[str] = []
        if name != "BaseModel" and "Config" in namespace:
            removed.append("class Config")
        for member_name, raw in namespace.items():
            function = raw.__func__ if isinstance(raw, classmethod) else raw
            if hasattr(function, "__removed_validator__"):
                removed.append(f"@validator on {member_name}")
            if hasattr(function, "__removed_root_validator__"):
                removed.append(f"@root_validator on {member_name}")
            if hasattr(function, "__field_validator__"):
                fields, mode = function.__field_validator__
                field_validators.append((fields, mode, function))
            if hasattr(function, "__model_validator__"):
                model_validators.append(function)
            if hasattr(function, "__field_serializer__"):
                for field_name in function.__field_serializer__:
                    serializers[field_name] = function
        model.__field_validators__ = field_validators
        model.__model_validators__ = model_validators
        model.__field_serializers__ = serializers
        model.__removed_apis__ = removed
        return model


class BaseModel(metaclass=ModelMeta):
    model_config = {}

    @classmethod
    def model_validate(cls, value: Mapping[str, object]):
        return cls._validate(value, json_mode=False)

    @classmethod
    def model_validate_json(cls, value: str | bytes):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValidationError(
                [{"type": "json_invalid", "loc": (), "msg": str(error), "input": value}]
            ) from error
        return cls._validate(decoded, json_mode=True)

    @classmethod
    def _validate(cls, value: Mapping[str, object], *, json_mode: bool):
        if cls.__removed_apis__:
            raise PydanticUserError(
                f"{cls.__name__} uses removed {cls.__removed_apis__[0]} API"
            )
        if not isinstance(value, Mapping):
            raise ValidationError(
                [{"type": "model_type", "loc": (), "msg": "Input should be a mapping", "input": value}]
            )

        config = cls.model_config
        strict = config.get("strict") is True
        populate_by_name = config.get("populate_by_name") is True
        loc_by_alias = config.get("loc_by_alias") is True
        allowed: set[str] = set()
        parsed: dict[str, object] = {}
        issues: list[dict[str, object]] = []

        for name, (expected_type, info) in cls.__model_fields__.items():
            alias = info.alias or name
            allowed.add(alias)
            if populate_by_name:
                allowed.add(name)
            location = (alias if loc_by_alias else name,)
            if alias in value:
                raw = value[alias]
            elif populate_by_name and name in value:
                raw = value[name]
            elif info.default is not _MISSING:
                raw = info.default
            else:
                issues.append(
                    {"type": "missing", "loc": location, "msg": "Field required", "input": value}
                )
                continue

            for fields, mode, function in cls.__field_validators__:
                if name in fields and mode == "before":
                    try:
                        raw = function(cls, raw)
                    except ValueError as error:
                        issues.append(
                            {"type": "value_error", "loc": location, "msg": str(error), "input": raw}
                        )
                        break
            else:
                try:
                    converted = cls._strict_value(expected_type, raw, strict, json_mode)
                except (TypeError, ValueError) as error:
                    issues.append(
                        {"type": cls._type_code(expected_type), "loc": location, "msg": str(error), "input": raw}
                    )
                    continue
                try:
                    for fields, mode, function in cls.__field_validators__:
                        if name in fields and mode == "after":
                            converted = function(cls, converted)
                except ValueError as error:
                    issues.append(
                        {"type": "value_error", "loc": location, "msg": str(error), "input": raw}
                    )
                    continue
                parsed[name] = converted

        if config.get("extra") == "forbid":
            for extra in value.keys() - allowed:
                issues.append(
                    {
                        "type": "extra_forbidden",
                        "loc": (extra,),
                        "msg": "Extra inputs are not permitted",
                        "input": value[extra],
                    }
                )
        if issues:
            raise ValidationError(issues)

        instance = object.__new__(cls)
        for name, parsed_value in parsed.items():
            setattr(instance, name, parsed_value)
        for function in cls.__model_validators__:
            try:
                result = function(instance)
            except ValueError as error:
                raise ValidationError(
                    [{"type": "value_error", "loc": (), "msg": str(error), "input": value}]
                ) from error
            if result is not instance:
                raise PydanticUserError("after model_validator must return self")
        return instance

    @staticmethod
    def _type_code(expected_type: type) -> str:
        return {
            int: "int_type",
            str: "string_type",
            bool: "bool_type",
            datetime: "datetime_type",
        }.get(expected_type, "is_instance_of")

    @staticmethod
    def _strict_value(expected_type: type, value: object, strict: bool, json_mode: bool):
        if expected_type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("Input should be a valid integer")
            return value
        if expected_type is bool:
            if not isinstance(value, bool):
                raise TypeError("Input should be a valid boolean")
            return value
        if expected_type is str:
            if not isinstance(value, str):
                raise TypeError("Input should be a valid string")
            return value
        if expected_type is datetime:
            if isinstance(value, datetime):
                return value
            if json_mode and isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            raise TypeError("Input should be a valid datetime")
        if strict and not isinstance(value, expected_type):
            raise TypeError(f"Input should be an instance of {expected_type.__name__}")
        return value

    def model_dump(self, *, by_alias: bool = False, mode: str = "python") -> dict[str, object]:
        output: dict[str, object] = {}
        for name, (_expected_type, info) in self.__model_fields__.items():
            value = getattr(self, name)
            serializer = self.__field_serializers__.get(name)
            if serializer is not None:
                value = serializer(self, value)
            elif mode == "json" and isinstance(value, datetime):
                value = value.isoformat()
            output[info.alias if by_alias and info.alias else name] = value
        return output

    def model_dump_json(self, *, by_alias: bool = False) -> str:
        return json.dumps(
            self.model_dump(by_alias=by_alias, mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def dict(self, **_kwargs):
        raise PydanticUserError("BaseModel.dict() is removed; use model_dump()")

    def json(self, **_kwargs):
        raise PydanticUserError("BaseModel.json() is removed; use model_dump_json()")
