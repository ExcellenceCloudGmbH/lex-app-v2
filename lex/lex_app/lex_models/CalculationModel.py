from abc import abstractmethod
import logging

from django.db import models
from django.db import transaction
from django_lifecycle import (
    hook,
    AFTER_UPDATE,
    AFTER_CREATE,
    BEFORE_SAVE,
)
from django_lifecycle.conditions import WhenFieldValueIs
from lex.lex_app.lex_models.LexModel import LexModel
from django.core.cache import caches
from lex.lex_app.rest_api.context import context_id
from lex.lex_app.logging.cache_manager import CacheManager

logger = logging.getLogger(__name__)


class CalculationModel(LexModel):

    IN_PROGRESS = "IN_PROGRESS"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"
    NOT_CALCULATED = "NOT_CALCULATED"
    ABORTED = "ABORTED"
    STATUSES = [
        (IN_PROGRESS, "IN_PROGRESS"),
        (ERROR, "ERROR"),
        (SUCCESS, "SUCCESS"),
        (NOT_CALCULATED, "NOT_CALCULATED"),
        (ABORTED, "ABORTED"),
    ]

    is_calculated = models.CharField(
        max_length=50, choices=STATUSES, default=NOT_CALCULATED, editable=False
    )

    class Meta:
        abstract = True

    @abstractmethod
    def update(self):
        pass

    # TODO: For the Celery task cases, this hook should be updated

    @hook(BEFORE_SAVE)
    def before_save(self):
        pass

        # Check if it's a new instance
        if self._state.adding:
            self.is_creation = True
        else:
            self.is_creation = False

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    @hook(AFTER_CREATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    def calculate_hook(self):
        from lex.lex_app.rest_api.signals import update_calculation_status

        try:
            if hasattr(self, "is_atomic") and not self.is_atomic:
                self.update()
                self.is_calculated = self.SUCCESS
            else:
                with transaction.atomic():
                    self.update()
                    self.is_calculated = self.SUCCESS

        except Exception as e:
            self.is_calculated = self.ERROR
            raise e
        finally:
            # Use the new CacheManager for cleanup instead of direct Redis operations
            try:
                calc_id = context_id.get()["calculation_id"]
                cleanup_result = CacheManager.cleanup_calculation(calc_id)
                if cleanup_result.success:
                    logger.info(f"Cache cleanup successful after calculation hook for calculation {calc_id}")
                else:
                    logger.warning(f"Cache cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors}")
            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")
                # Fallback to old method if new method fails
                try:
                    redis_cache = caches["redis"]
                    calc_id = context_id.get()["calculation_id"]
                    cache_key = f"calculation_log_{calc_id}"
                    redis_cache.delete(cache_key)
                except Exception as fallback_error:
                    logger.error(f"Fallback cache cleanup also failed: {str(fallback_error)}")
            
            self.save(skip_hooks=True)
            update_calculation_status(self)

