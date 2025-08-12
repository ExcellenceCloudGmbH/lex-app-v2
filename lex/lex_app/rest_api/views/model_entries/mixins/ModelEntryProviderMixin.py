from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.exceptions import APIException

from lex.lex_app.logging.CalculationLog import (
    CalculationLog,
)  # Import your CalculationLog model
from lex.lex_app.rest_api.views.permissions.UserPermission import UserPermission


class ModelEntryProviderMixin:
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def get_queryset(self):
        return self.kwargs["model_container"].model_class.objects.all()

    def get_serializer_class(self):
        """
        Chooses serializer based on `?serializer=<name>`, defaulting to 'default'.
        """
        container = self.kwargs["model_container"]
        choice = self.request.query_params.get("serializer", "default")
        mapping = container.serializers_map

        if choice not in mapping:
            raise APIException(
                {
                    "error": f"Unknown serializer '{choice}' for model '{container.model_class._meta.model_name}'",
                    "available": list(mapping.keys()),
                }
            )

        return mapping[choice]
