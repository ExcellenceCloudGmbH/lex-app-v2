from typing import Set
from django.db.models import Model
from lex.lex_app.lex_models.ModelModificationRestriction import (
    ModelModificationRestriction,
)
from lex.lex_app.rest_api.serializers import get_serializer_map_for_model


class ModelContainer:
    def __init__(self, model_class: Model, process_admin) -> None:
        self.model_class = model_class
        self.process_admin = process_admin
        self.dependent_model_containers: Set["ModelContainer"] = set()

        if hasattr(model_class, "_meta"):
            # Build and store all serializers for this model
            default_fields = process_admin.get_fields_in_table_view(model_class)
            self.serializers_map = get_serializer_map_for_model(
                model_class, default_fields
            )

            # The one used by default in list/detail endpoints
            self.obj_serializer = self.serializers_map.get("default")

    @property
    def id(self) -> str:
        return (
            self.model_class._meta.model_name
            if hasattr(self.model_class, "_meta")
            else self.model_class.__name__.lower()
        )

    @property
    def title(self) -> str:
        if hasattr(self.model_class, "_meta"):
            from .utils import title_for_model

            return title_for_model(self.model_class)
        return self.model_class.__name__

    @property
    def pk_name(self) -> str:
        return (
            self.model_class._meta.pk.name
            if hasattr(self.model_class, "_meta")
            else None
        )

    def get_modification_restriction(self):
        return getattr(
            self.model_class, "modification_restriction", ModelModificationRestriction()
        )

    def get_general_modification_restrictions_for_user(self, user):
        r = self.get_modification_restriction()
        return {
            "can_read_in_general": r.can_read_in_general(user, None),
            "can_modify_in_general": r.can_modify_in_general(user, None),
            "can_create_in_general": r.can_create_in_general(user, None),
            "can_delete_in_general": r.can_delete_in_general(user, None),
        }
