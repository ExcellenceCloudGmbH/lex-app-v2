from rest_framework import serializers, viewsets

# -- Base template that provides `short_description` --
class RestApiModelSerializerTemplate(serializers.ModelSerializer):
    short_description = serializers.SerializerMethodField()

    def get_short_description(self, obj):
        return str(obj)

    class Meta:
        model = None
        fields = "__all__"


class RestApiModelViewSetTemplate(viewsets.ModelViewSet):
    queryset = None
    serializer_class = None


# Field‐names that React-Admin expects
ID_FIELD_NAME = "id_field"
SHORT_DESCR_NAME = "short_description"


def model2serializer(model, fields=None, name_suffix=""):
    """
    Auto-generate a ModelSerializer for `model`,
    including id_field, short_description, and id.
    """
    if not hasattr(model, "_meta"):
        return None

    if fields is None:
        fields = [f.name for f in model._meta.fields]

    model_name = model._meta.model_name.capitalize()
    class_name = (
        f"{model_name}{name_suffix.capitalize()}Serializer"
        if name_suffix
        else f"{model_name}Serializer"
    )

    # alias for model._meta.pk.name
    pk_alias = serializers.ReadOnlyField(default=model._meta.pk.name)

    all_fields = list(fields) + [ID_FIELD_NAME, SHORT_DESCR_NAME, "id"]

    return type(
        class_name,
        (RestApiModelSerializerTemplate,),
        {
            # add our alias‐and‐description fields
            ID_FIELD_NAME: pk_alias,
            "Meta": type(
                "Meta",
                (RestApiModelSerializerTemplate.Meta,),
                {"model": model, "fields": all_fields},
            ),
        },
    )


def _wrap_custom_serializer(custom_cls, model_class):
    """
    Create a subclass of the user‐provided serializer that adds
    id_field and short_description into its fields/Meta.
    """
    # 1) build or extend its Meta
    meta = getattr(custom_cls, "Meta", type("Meta", (), {}))
    existing_fields = getattr(meta, "fields", "__all__")

    if existing_fields != "__all__":
        # ensure lists are mutable copies
        existing = list(existing_fields)
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id"):
            if extra not in existing:
                existing.append(extra)
        new_fields = existing
    else:
        new_fields = "__all__"

    NewMeta = type("Meta", (meta,), {"model": model_class, "fields": new_fields})

    # 2) define the extra DRF fields
    attrs = {
        ID_FIELD_NAME: serializers.ReadOnlyField(default=model_class._meta.pk.name),
        SHORT_DESCR_NAME: serializers.SerializerMethodField(),
        "get_short_description": lambda self, obj: str(obj),
        "Meta": NewMeta,
    }

    # 3) build the new subclass
    return type(f"{custom_cls.__name__}WithInternalFields", (custom_cls,), attrs)


def get_serializer_map_for_model(model_class, default_fields=None):
    """
    Returns a dict name→SerializerClass for `model_class`:
      1) If model_class.api_serializers is a non-empty dict, wrap each one so it
         also has id_field & short_description.
      2) Else if it's a CalculationLog subclass, use the existing
         CalculationLogSerializer (which already includes our extras).
      3) Otherwise auto-generate a single 'default' serializer.
    """
    # 1) Custom per-model serializers?
    custom = getattr(model_class, "api_serializers", None)
    if isinstance(custom, dict) and custom:
        wrapped = {}
        for name, cls in custom.items():
            wrapped[name] = _wrap_custom_serializer(cls, model_class)
        return wrapped

    # 2) Fallback to auto-generated
    auto = model2serializer(model_class, default_fields)
    return {"default": auto}
