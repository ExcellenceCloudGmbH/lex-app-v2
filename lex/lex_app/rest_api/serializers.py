from django.db import models
from rest_framework import serializers, viewsets

from lex_app.lex_models.LexModel import LexModel

# Field‐names that React-Admin expects
ID_FIELD_NAME = "id_field"
SHORT_DESCR_NAME = "short_description"



# --- NEW FILTERING LIST SERIALIZER ---
class FilteredListSerializer(serializers.ListSerializer):
    """
    A custom ListSerializer that filters out items that, after serialization,
    result in an empty dictionary.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, models.Manager) else data
        ret = []
        for item in iterable:
            representation = self.child.to_representation(item)
            # Only include non-empty results in the final list
            if representation:
                ret.append(representation)
        return ret


# --- UPDATED PERMISSION-AWARE BASE SERIALIZER ---
class LexSerializer(serializers.ModelSerializer):
    """
    A custom ModelSerializer that controls field visibility and adds a
    `scopes` field to the output for each record.
    """
    # Define a new field to hold thescopes for each record.
    lex_reserved_scopes = serializers.SerializerMethodField()


    def get_lex_reserved_scopes(self, instance):
        """
        This method is called for each record to get its specific permissions.
        It calls the can_* methods on the model instance.
        """
        request = self.context.get('request')
        if not request:
            return {}

        lexmodel_fields =  set(map(lambda x: x.name, LexModel._meta.fields))
        edit = instance.can_edit(request) - lexmodel_fields - {'id'}
        delete = instance.can_delete(request)
        export = instance.can_export(request)
        if not edit:
            edit = []

        return {
            "edit": edit,
            "delete": delete,
            "export": export
        }

    def to_representation(self, instance):
        """
        This method completely reshapes the output to combine field values
        and their associated permissions.
        """
        request = self.context.get('request')

        # The can_read method from LexModel returns the set of visible fields.
        visible_fields = instance.can_read(request)
        if not visible_fields:
            return {}

        # 2. Get the default flat representation of the instance.
        # This gives us the raw values for all fields.
        representation = super().to_representation(instance)

        # Filter the representation to only include visible fields.
        # The `scopes` field is always included if the record is visible.
        for field_name in list(representation.keys()):
            if field_name not in visible_fields and field_name != 'lex_reserved_scopes' and field_name not in ['id', 'id_field', SHORT_DESCR_NAME]:
                representation.pop(field_name)

        return representation


# --- UPDATED BASE TEMPLATE ---
class RestApiModelSerializerTemplate(LexSerializer):
    """
    The base template for all auto-generated and wrapped serializers.
    It inherits the new nested permission structure from LexSerializer.
    """
    # Note: short_description is now implicitly handled by the parent's
    # to_representation method and will be nested like all other fields.
    short_description = serializers.SerializerMethodField()

    def get_short_description(self, obj):
        return str(obj)

    class Meta:
        model = None
        fields = "__all__"
        # Use our custom list serializer for all list views.
        list_serializer_class = FilteredListSerializer


class RestApiModelViewSetTemplate(viewsets.ModelViewSet):
    queryset = None
    serializer_class = None


# --- HELPER FUNCTIONS (Unchanged) ---

def model2serializer(model, fields=None, name_suffix=""):
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
            ID_FIELD_NAME: pk_alias,
            "Meta": type(
                "Meta",
                (RestApiModelSerializerTemplate.Meta,),
                {"model": model, "fields": all_fields},
            ),
        },
    )


def _wrap_custom_serializer(custom_cls, model_class):
    meta = getattr(custom_cls, "Meta", type("Meta", (), {}))
    existing_fields = getattr(meta, "fields", "__all__")
    if existing_fields != "__all__":
        existing = list(existing_fields)
        for extra in (ID_FIELD_NAME, SHORT_DESCR_NAME, "id"):
            if extra not in existing:
                existing.append(extra)
        new_fields = existing
    else:
        new_fields = "__all__"
    NewMeta = type("Meta", (meta,),
                   {"model": model_class, "fields": new_fields, "list_serializer_class": FilteredListSerializer})
    attrs = {
        ID_FIELD_NAME: serializers.ReadOnlyField(default=model_class._meta.pk.name),
        SHORT_DESCR_NAME: serializers.SerializerMethodField(),
        "get_short_description": lambda self, obj: str(obj),
        "Meta": NewMeta,
    }
    base_classes = (LexSerializer, custom_cls)
    return type(f"{custom_cls.__name__}WithInternalFields", base_classes, attrs)


def get_serializer_map_for_model(model_class, default_fields=None):
    custom = getattr(model_class, "api_serializers", None)
    if isinstance(custom, dict) and custom:
        wrapped = {}
        for name, cls in custom.items():
            wrapped[name] = _wrap_custom_serializer(cls, model_class)
        return wrapped
    auto = model2serializer(model_class, default_fields)
    return {"default": auto}
