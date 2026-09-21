import jsonschema
from django.core.exceptions import ValidationError


def validate_specification(category, specification: dict) -> None:
    """
    Validate a Product.specification dict against its Category.spec_schema.
    A blank/empty schema means "no constraints" (e.g. a category that hasn't
    been assigned a schema yet) -- validation is a no-op in that case.
    """
    schema = category.spec_schema if category else None
    if not schema:
        return

    try:
        jsonschema.validate(instance=specification, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        path = " -> ".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValidationError(f"Niepoprawna specyfikacja w polu {path}: {exc.message}")
