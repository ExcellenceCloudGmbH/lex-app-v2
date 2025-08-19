import traceback
import logging

from django.db import transaction
from lex.lex_app.logging.model_context import model_logging_context
from rest_framework.exceptions import APIException
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin

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
from lex.lex_app.logging.cache_manager import CacheManager

logger = logging.getLogger(__name__)


class OneModelEntry(
    AuditLogMixin,
    ModelEntryProviderMixin,
    DestroyOneWithPayloadMixin,
    RetrieveUpdateDestroyAPIView,
    CreateAPIView,
):
    def create(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]

        calculationId = self.kwargs["calculationId"]

        with OperationContext(request, calculationId) as context_id:

            try:
                with transaction.atomic():
                    response = CreateModelMixin.create(self, request, *args, **kwargs)
                
                # Cache cleanup after successful create operation
                try:
                    cleanup_result = CacheManager.cleanup_calculation(calculationId)
                    if cleanup_result.success:
                        logger.info(f"Cache cleanup successful after create operation for calculation {calculationId}")
                    else:
                        logger.warning(f"Cache cleanup had errors after create operation for calculation {calculationId}: {cleanup_result.errors}")
                except Exception as cleanup_error:
                    logger.error(f"Cache cleanup failed after create operation for calculation {calculationId}: {str(cleanup_error)}")
                
            except Exception as e:
                # Cache cleanup for failed create operation
                try:
                    cleanup_result = CacheManager.cleanup_calculation(calculationId)
                    logger.info(f"Cache cleanup performed after failed create operation for calculation {calculationId}")
                except Exception as cleanup_error:
                    logger.error(f"Cache cleanup failed after failed create operation for calculation {calculationId}: {str(cleanup_error)}")
                
                raise APIException(
                    {"error": f"{e} ", "traceback": traceback.format_exc()}
                )

            return response

    def update(self, request, *args, **kwargs):

        model_container = self.kwargs["model_container"]
        calculationId = self.kwargs["calculationId"]

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
                    
                    # Cache cleanup after successful update operation
                    try:
                        cleanup_result = CacheManager.cleanup_calculation(calculationId)
                        if cleanup_result.success:
                            logger.info(f"Cache cleanup successful after update operation for calculation {calculationId}")
                        else:
                            logger.warning(f"Cache cleanup had errors after update operation for calculation {calculationId}: {cleanup_result.errors}")
                    except Exception as cleanup_error:
                        logger.error(f"Cache cleanup failed after update operation for calculation {calculationId}: {str(cleanup_error)}")

                except Exception as e:
                    # Cache cleanup for failed update operation
                    try:
                        cleanup_result = CacheManager.cleanup_calculation(calculationId)
                        logger.info(f"Cache cleanup performed after failed update operation for calculation {calculationId}")
                    except Exception as cleanup_error:
                        logger.error(f"Cache cleanup failed after failed update operation for calculation {calculationId}: {str(cleanup_error)}")

                    raise APIException(
                        {"error": f"{e} ", "traceback": traceback.format_exc()}
                    )

                # TODO: For sharepoint preview, find a new way to create an audit log with the new structure
                # if "edited_file" in request.data:

                return response
