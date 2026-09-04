"""
Tool manifests — the declarative contract every tool must publish.

A tool without a manifest cannot execute. The manifest is what the
permission layer, risk engine, and policy engine reason about; the executor
is just the last step after all of them said yes.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


CAPABILITIES = ("read", "write", "execute", "network", "destructive")


class ToolManifest(BaseModel):
    """Declarative description of one tool."""

    name: str = Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")  # e.g. "github.merge_pr"
    version: str = "1.0.0"
    description: str
    owner: str = "helios-core"

    # What kind of effect this tool has on the world.
    capability: str = "read"  # read | write | execute | network | destructive

    # JSON-schema-subset for arguments (type/properties/required/enum).
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})

    # Baseline risk class before contextual adjustment.
    risk_class: str = "low"  # low | medium | high | critical

    # Permission scopes required to invoke, e.g. ["github.write"].
    scopes: list[str] = Field(default_factory=list)

    # Which argument fields identify the target resource, mapped to the
    # canonical resource field name used in permission constraints:
    #   {"repo": "github.repo", "branch": "github.branch"}
    resource_fields: dict[str, str] = Field(default_factory=dict)

    # Network access requirements (empty = no network).
    network: list[str] = Field(default_factory=list)  # domains the executor may reach

    # never | risk_based | always
    approval: str = "risk_based"

    # Whether repeated execution with identical args is safe.
    idempotent: bool = False

    # Whether an approver may edit arguments before approving.
    args_editable: bool = False

    # builtin | mcp | custom — where the tool came from.
    provenance: str = "builtin"

    def extract_resource(self, args: dict) -> dict:
        """Pull the target resource fields out of a concrete argument payload."""
        resource: dict[str, Any] = {}
        for arg_field, canonical in self.resource_fields.items():
            if arg_field in args and args[arg_field] is not None:
                resource[canonical] = args[arg_field]
        return resource

    def public_dict(self) -> dict:
        return self.model_dump()


def validate_args(schema: dict, args: Any, path: str = "args") -> list[str]:
    """
    Validate `args` against a JSON-schema subset (type, properties, required,
    enum, additionalProperties). Returns a list of human-readable errors —
    empty means valid. Deterministic, zero-dependency.
    """
    errors: list[str] = []
    expected = schema.get("type")

    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
    }

    if expected == "integer":
        if not isinstance(args, int) or isinstance(args, bool):
            return [f"{path}: expected integer, got {type(args).__name__}"]
    elif expected == "number":
        if not isinstance(args, (int, float)) or isinstance(args, bool):
            return [f"{path}: expected number, got {type(args).__name__}"]
    elif expected in type_map and not isinstance(args, type_map[expected]):
        return [f"{path}: expected {expected}, got {type(args).__name__}"]

    if "enum" in schema and args not in schema["enum"]:
        errors.append(f"{path}: value {args!r} not in {schema['enum']}")

    if expected == "object" and isinstance(args, dict):
        props: dict = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in args:
                errors.append(f"{path}.{req}: required field missing")
        if schema.get("additionalProperties") is False:
            for key in args:
                if key not in props:
                    errors.append(f"{path}.{key}: unexpected field")
        for key, sub in props.items():
            if key in args:
                errors.extend(validate_args(sub, args[key], f"{path}.{key}"))

    if expected == "array" and isinstance(args, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(args):
                errors.extend(validate_args(item_schema, item, f"{path}[{i}]"))

    return errors


class Tool:
    """A manifest bound to its executor. Only the broker ever calls `execute`."""

    def __init__(
        self,
        manifest: ToolManifest,
        executor: Callable[..., dict],
        resource_fn: Callable[[dict], dict] | None = None,
    ):
        self.manifest = manifest
        self._executor = executor
        self._resource_fn = resource_fn

    def resource(self, args: dict) -> dict:
        """
        Deterministic resource extraction: manifest field mapping plus an
        optional tool-specific resolver (e.g. path normalization so `../`
        cannot slip past prefix constraints).
        """
        resource = self.manifest.extract_resource(args)
        if self._resource_fn is not None:
            resource.update(self._resource_fn(args))
        return resource

    def execute(self, args: dict, context) -> dict:
        return self._executor(args, context)
