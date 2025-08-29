import traceback

from django.db import transaction
from rest_framework_api_key.permissions import HasAPIKey

from lex.lex_app.logging.model_context import model_logging_context
from rest_framework.exceptions import APIException
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin

from rest_framework.response import Response
from rest_framework import status
# import CalculationModel
from lex.lex_app.lex_models.CalculationModel import CalculationModel

# import update_calculation_status
from lex.lex_app.rest_api.signals import update_calculation_status
from lex.lex_app.logging.AuditLogMixin import AuditLogMixin
from lex.lex_app.rest_api.context import OperationContext
from lex.lex_app.rest_api.views.model_entries.mixins.DestroyOneWithPayloadMixin import (
    DestroyOneWithPayloadMixin,
)
from lex.lex_app.rest_api.views.model_entries.mixins.ModelEntryProviderMixin import (
    ModelEntryProviderMixin,
)
from rest_framework.permissions import IsAuthenticated

from lex_app.rest_api.views.permissions.UserPermission import UserPermission


class OneModelEntry(
    AuditLogMixin,
    ModelEntryProviderMixin,
    DestroyOneWithPayloadMixin,
    RetrieveUpdateDestroyAPIView,
    CreateAPIView,
):
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        # instance = model_container.model_class()
        # if not instance.can_create(request):
        #     return Response(data={}, status=status.HTTP_403_FORBIDDEN, headers={})

        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId) as context_id:

            try:
                with transaction.atomic():
                    response = CreateModelMixin.create(self, request, *args, **kwargs)
            except Exception as e:
                raise APIException(
                    {"error": f"{e} ", "traceback": traceback.format_exc()}
                )

            return response

    def update(self, request, *args, **kwargs):

        model_container = self.kwargs["model_container"]
        calculationId = self.kwargs["calculationId"]
        instance = model_container.model_class()
        # if not instance.can_edit(request):
        #     return Response(data={}, status=status.HTTP_403_FORBIDDEN, headers={})

        with OperationContext(request, calculationId) as context_id:
            instance = model_container.model_class.objects.filter(
                pk=self.kwargs["pk"]
            ).first()
            with model_logging_context(instance):
                if "calculate" in request.data and request.data["calculate"] == "true":
                    # instance = model_container.model_class.objects.filter(pk=self.kwargs["pk"]).first()
                    instance.is_calculated = CalculationModel.IN_PROGRESS
                    instance.save(skip_hooks=True)
                    update_calculation_status(instance)

                # TODO: For sharepoint preview, find a new way to create an audit log with the new structure
                # if "edited_file" not in request.data:

                try:
                    response = UpdateModelMixin.update(self, request, *args, **kwargs)

                except Exception as e:

                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    )

                # TODO: For sharepoint preview, find a new way to create an audit log with the new structure
                # if "edited_file" in request.data:

                return response
