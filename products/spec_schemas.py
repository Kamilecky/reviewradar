"""
Default JSON Schema (draft-07) templates for Product.specification, one per
Category.MainCategory. A Category with a blank spec_schema is seeded from
here on save (see Category.save()); admins can then customize a specific
category's schema (e.g. add "screen_size_inches" for Laptopy) without
touching this file.

brand/model are deliberately excluded here since Product already has
top-level brand/model_name fields for every category -- these schemas only
cover the category-specific attributes that live inside specification.

All schemas are permissive (no "required", additionalProperties True) so
they document expected shape without retroactively invalidating existing or
hand-entered data that doesn't use every field.

Keyed by the plain string values of Category.MainCategory (not the enum
class itself) to avoid a models.py <-> spec_schemas.py circular import --
Category.save() imports SPEC_SCHEMAS, so this module must not import models.
"""

ELECTRONICS_SCHEMA = {
    "type": "object",
    "properties": {
        "technical_parameters": {
            "type": "object",
            "description": "Free-form key/value technical specs, e.g. {'RAM': '16GB', 'CPU': '...'}",
        },
        "warranty_months": {"type": "integer", "minimum": 0},
        "compatibility": {"type": "string"},
    },
    "additionalProperties": True,
}

COSMETICS_SCHEMA = {
    "type": "object",
    "properties": {
        "product_type": {
            "type": "string",
            "enum": ["pielegnacja", "makijaz", "higiena"],
        },
        "ingredients_inci": {"type": "string"},
        "capacity_ml": {"type": "number", "minimum": 0},
        "intended_use": {"type": "string"},
        "dermatologically_tested": {"type": "boolean"},
    },
    "additionalProperties": True,
}

HOUSEHOLD_SCHEMA = {
    "type": "object",
    "properties": {
        "material": {"type": "string"},
        "dimensions": {"type": "string"},
        "room": {"type": "string"},
        "certificates": {"type": "string"},
    },
    "additionalProperties": True,
}

SPEC_SCHEMAS = {
    "electronics": ELECTRONICS_SCHEMA,
    "cosmetics": COSMETICS_SCHEMA,
    "household": HOUSEHOLD_SCHEMA,
}
