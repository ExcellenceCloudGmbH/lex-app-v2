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
from lex.lex_app.rest_api.context import operation_context
from lex.lex_app.logging.cache_manager import CacheManager
from lex.lex_app.logging.model_context import model_logging_context
from lex_app.logging.context_resolver import ContextResolver

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

    @hook(BEFORE_SAVE)
    def before_save(self):
        pass

        # Check if it's a new instance
        if self._state.adding:
            self.is_creation = True
        else:
            self.is_creation = False

    def should_use_celery(self) -> bool:
        """
        Determine if calculation should use Celery based on configuration and availability.

        Returns:
            bool: True if Celery should be used, False for synchronous execution
        """
        from lex.lex_app import settings

        # Check if Celery is enabled in setting
        if not getattr(settings, 'CELERY_ACTIVE', False):
            return False

        # Check if Celery is available by trying to import and test connection
        try:
            from celery import current_app
            # Test if we can access Celery (this will fail if broker is down)
            current_app.control.inspect()
            return True
        except Exception:
            # Celery not available, fall back to synchronous execution
            return False

    def dispatch_calculation_task(self):
        """
        Dispatch calculation to Celery worker using the calc_and_save task.

        Returns:
            AsyncResult: Celery task result object
        """
        from lex.lex_app.celery_tasks import calc_and_save

        # Extract only the calculation_id from context to avoid pickling issues
        calculation_id = None
        try:
            context = context_id.get()
            if context and "calculation_id" in context:
                calculation_id = context["calculation_id"]
        except Exception as e:
            logger.warning(f"Could not get calculation_id from context: {e}")

        # Dispatch single model calculation to Celery with calculation_id
        return calc_and_save.delay([self], calculation_id=calculation_id)

    def execute_calculation_sync(self):
        """
        Execute calculation synchronously in the current thread.
        """
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
            # Clean up cache if context is available
            try:
                context = ContextResolver.resolve()
                calc_id = context.calculation_id
                key_to_clean = CacheManager.build_cache_key(context.calculation_id, context.current_record)
                cleanup_result = CacheManager.cleanup_calculation(context.calculation_id, specific_keys=[key_to_clean])

                if cleanup_result.success:
                    logger.info(f"Cache cleanup successful after calculation hook for calculation {calc_id}")
                else:
                    logger.warning(f"Cache cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors}")
            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")

            self.save(skip_hooks=True)
            update_calculation_status(self)

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    @hook(AFTER_CREATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))
    def calculate_hook(self):
        """
        Enhanced calculation hook with Celery integration.

        Dispatches calculations to Celery workers when celery_active=True and Celery
        is available, otherwise falls back to synchronous execution. Proper status
        management ensures IN_PROGRESS -> SUCCESS/ERROR transitions.
        """
        from lex.lex_app.rest_api.signals import update_calculation_status
        import logging

        logger = logging.getLogger(__name__)

        try:
            if self.should_use_celery():
                # Dispatch to Celery worker
                logger.info(f"Dispatching calculation for {self} to Celery worker")
                # self.context = context_id.get()
                task_result = self.dispatch_calculation_task()

                # Store task ID if the model has a task_id field
                if hasattr(self, 'task_id'):
                    self.task_id = task_result.id
                    self.save(skip_hooks=True)

                # Note: Status will be updated by CallbackTask.on_success/on_failure
                # Model remains in IN_PROGRESS state until task completes
                logger.info(f"Calculation task {task_result.id} dispatched for {self}")

            else:
                # Execute synchronously as fallback
                logger.info(f"Executing calculation for {self} synchronously (Celery not available)")
                self.execute_calculation_sync()

        except Exception as e:
            # Handle any errors in task dispatch or synchronous execution
            logger.error(f"Calculation failed for {self}: {e}", exc_info=True)
            self.is_calculated = self.ERROR

            # Store error message if the model has an error_message field
            if hasattr(self, 'error_message'):
                self.error_message = str(e)

            # Clean up cache and save error state
            try:
                context = ContextResolver.resolve()
                calc_id = context.calculation_id
                key_to_clean = CacheManager.build_cache_key(context.calculation_id, context.current_record)
                cleanup_result = CacheManager.cleanup_calculation(context.calculation_id, specific_keys=[key_to_clean])

                if cleanup_result.success:
                    logger.info(f"Cache cleanup successful after calculation hook for calculation {calc_id}")
                else:
                    logger.warning(f"Cache cleanup had errors after calculation hook for calculation {calc_id}: {cleanup_result.errors}")
            except Exception as cleanup_error:
                logger.error(f"Cache cleanup failed after calculation hook: {str(cleanup_error)}")
                # Fallback to old method if new method fails

            self.save(skip_hooks=True)
            update_calculation_status(self)
            raise e
