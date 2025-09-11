"""
Celery task infrastructure with custom decorators and callback handling.

This module provides enhanced Celery task integration with proper lifecycle
management, status tracking, and error handling for calculation models.
"""

import logging
from copy import deepcopy
from functools import wraps
from typing import Dict, Tuple
from uuid import uuid4

from celery import Task, shared_task
from celery.result import allow_join_result

from lex.lex_app.logging.model_context import _model_context, model_logging_context
from celery.signals import task_postrun
from django.db import transaction
from django.db.models import Model

from lex.lex_app.lex_models.CalculationModel import CalculationModel
from lex.lex_app.rest_api.signals import update_calculation_status
from lex.lex_app.rest_api.context import operation_context, OperationContext
from celery.app.control import Control
import threading
from contextlib import contextmanager
from typing import List, Optional, Set, Callable, Any



logger = logging.getLogger(__name__)

@task_postrun.connect
def task_done(sender=None, task_id=None, task=None, args=None, kwargs=None, **kw):
    control = Control(app=task.app)
    control.shutdown()


class CeleryCalculationContext:
    """
    Context manager to set calculation_id for Celery tasks.
    
    This allows CalculationLog.log() to access the calculation_id
    even when running in a Celery worker process.
    """
    
    def __init__(self, context, model_context):
        self.context = context
        self.model_context = model_context
    
    def __enter__(self):
        if self.context :
            logger.warning(f"Operation Context {self.context}")

            new_context = deepcopy(self.context)
            new_context['calculation_id'] = self.context.get('calculation_id', None)
            new_context['operation_id'] = str(uuid4())
            new_context["celery_task"] =  True
            new_context["task_name"] =  "calc_and_save"

            operation_context.set(new_context)
        if self.model_context:
            _model_context.get()['model_context'] = self.model_context
            logger.warning(f"Operation Context {self.model_context}")
            logger.warning(f"Saved context {_model_context.get()['model_context']}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass





class CallbackTask(Task):
    """
    Enhanced Celery Task class with proper callback handling for calculation models.
    
    Provides automatic status updates and error handling for calculation tasks,
    with special handling for the initial_data_upload task.
    """
    
    def on_success(self, retval: Any, task_id: str, args: Tuple, kwargs: Dict) -> None:
        """
        Handle successful task completion.
        
        Updates model status to SUCCESS and notifies connected systems via WebSocket.
        Skips status updates for initial_data_upload tasks.
        
        Args:
            retval: The return value of the task
            task_id: Unique task identifier
            args: Task arguments (first arg should be model instance or list of models)
            kwargs: Task keyword arguments
        """
        try:
            # Skip callback for initial_data_upload tasks
            if self.name == "initial_data_upload":
                return
                
            # Extract model instances from task arguments
            model_instances = self._extract_model_instances(args)

            for model_instance in model_instances:
                if isinstance(model_instance, CalculationModel):
                    self._update_model_status(
                        model_instance, 
                        CalculationModel.SUCCESS, 
                        task_id=task_id
                    )

        except Exception as callback_error:
            logger.error(
                f"Success callback failed for task {task_id}: {callback_error}",
                exc_info=True
            )
    
    def on_failure(self, exc: Exception, task_id: str, args: Tuple, kwargs: Dict, einfo: Any) -> None:
        """
        Handle task failure.
        
        Updates model status to ERROR, stores error information, and notifies
        connected systems. Skips status updates for initial_data_upload tasks.
        
        Args:
            exc: The exception that caused the failure
            task_id: Unique task identifier
            args: Task arguments (first arg should be model instance or list of models)
            kwargs: Task keyword arguments
            einfo: Exception info object
        """
        try:
            # Skip callback for initial_data_upload tasks
            if self.name == "initial_data_upload":
                return
                
            # Extract model instances from task arguments
            model_instances = self._extract_model_instances(args)
            
            for model_instance in model_instances:
                if isinstance(model_instance, CalculationModel):
                    self._update_model_status(
                        model_instance, 
                        CalculationModel.ERROR, 
                        error_message=str(exc),
                        task_id=task_id
                    )
                    
        except Exception as callback_error:
            logger.error(
                f"Failure callback failed for task {task_id}: {callback_error}",
                exc_info=True
            )
    
    def _extract_model_instances(self, args: Tuple) -> List[Model]:
        """
        Extract model instances from task arguments.
        
        Handles both single model instances and lists of models.
        
        Args:
            args: Task arguments tuple
            
        Returns:
            List of model instances
        """
        model_instances = []
        
        if args:
            first_arg = args[0]
            if isinstance(first_arg, Model):
                model_instances = [first_arg]
            elif isinstance(first_arg, (list, tuple)):
                model_instances = [item for item in first_arg if isinstance(item, Model)]
                
        return model_instances
    
    def _update_model_status(
        self, 
        model_instance: CalculationModel, 
        status: str, 
        error_message: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> None:
        """
        Update model status and notify connected systems.
        
        Args:
            model_instance: The model instance to update
            status: New calculation status
            error_message: Error message if status is ERROR
            task_id: Task ID for tracking
        """
        try:
            with transaction.atomic():
                model_instance.is_calculated = status
                
                # Store error information if provided
                if error_message and hasattr(model_instance, 'error_message'):
                    model_instance.error_message = error_message
                    
                # Store task ID if provided and field exists
                if task_id and hasattr(model_instance, 'task_id'):
                    model_instance.task_id = task_id

                # Save without triggering hooks to prevent recursion
                model_instance.save(skip_hooks=True)

                logger.warning(f"Updating status for {model_instance.__class__.__name__} task {task_id}")
                # Notify connected systems via WebSocket
                update_calculation_status(model_instance)
                
        except Exception as update_error:
            logger.error(
                f"Failed to update model status for {model_instance}: {update_error}",
                exc_info=True
            )


class RunInCelery:
    """
    Context manager that selectively dispatches lex_shared_task decorated functions
    to Celery workers while keeping others synchronous.
    """

    # Thread-local storage for the active context
    _local = threading.local()

    def __init__(self, include_tasks: Optional[Set[str]] = None,
                 exclude_tasks: Optional[Set[str]] = None):
        """
        Initialize the context manager.

        Args:
            include_tasks: Set of task names to dispatch (if None, dispatch all lex_shared_tasks)
            exclude_tasks: Set of task names to keep synchronous (overrides include_tasks)
        """
        self.include_tasks = include_tasks
        self.exclude_tasks = exclude_tasks or set()
        self.dispatched_results: List[Any] = []

    def __enter__(self):
        # Store the context in thread-local storage
        if not hasattr(self._local, 'contexts'):
            self._local.contexts = []
        self._local.contexts.append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Remove this context from thread-local storage
        if hasattr(self._local, 'contexts') and self._local.contexts:
            self._local.contexts.pop()

        # Wait for all dispatched tasks to complete
        self.wait_for_completion()

    def should_dispatch(self, task_name: str) -> bool:
        """Determine if a task should be dispatched based on include/exclude rules."""
        if task_name in self.exclude_tasks:
            return False
        if self.include_tasks is None:
            return True
        return task_name in self.include_tasks

    def add_dispatched_result(self, result):
        """Add a dispatched task result to track for completion."""
        self.dispatched_results.append(result)

    def wait_for_completion(self):
        """Wait for all dispatched tasks to complete."""
        logger.info(f"Waiting for {len(self.dispatched_results)} dispatched tasks to complete")
        for result in self.dispatched_results:
            try:
                # This will block until the task completes
                with allow_join_result():
                    result.get()

                logger.debug(f"Task {result.id} completed successfully")
            except Exception as e:
                logger.error(f"Task {result.id} failed: {e}")
                # You might want to re-raise or handle this differently
                raise
        logger.info("All dispatched tasks completed")

    @classmethod
    def get_current_context(cls) -> Optional['RunInCelery']:
        """Get the current active context from thread-local storage."""
        if hasattr(cls._local, 'contexts') and cls._local.contexts:
            return cls._local.contexts[-1]  # Return the most recent context
        return None


# Enhanced BoundTaskMethod that respects the RunInCelery context
class EnhancedBoundTaskMethod:
    """
    Enhanced version of BoundTaskMethod that checks for RunInCelery context
    and dispatches tasks accordingly.
    """

    def __init__(self, instance, task):
        self.instance = instance
        self.task = task

    def __call__(self, *args, **kwargs):
        """Handles direct calls - checks context to decide sync vs async execution."""
        context = RunInCelery.get_current_context()

        if context is None:
            # No context - run synchronously
            return self.task(self.instance, *args, **kwargs)

        # Check if this task should be dispatched
        task_name = getattr(self.task, 'name', self.task.__name__)

        if context.should_dispatch(task_name):
            # Dispatch asynchronously - IMPORTANT: prepend self.instance to args
            logger.debug(f"Dispatching task {task_name} to Celery")
            result = self.task.delay(self.instance, *args, **kwargs)
            context.add_dispatched_result(result)
            return result
        else:
            # Run synchronously
            logger.debug(f"Running task {task_name} synchronously")
            return self.task(self.instance, *args, **kwargs)

    def delay(self, *args, **kwargs):
        """Always handles asynchronous .delay() calls."""
        return self.task.delay(self.instance, *args, **kwargs)

    def apply_async(self, args=None, kwargs=None, **options):
        """Always handles asynchronous .apply_async() calls."""
        args = list(args) if args is not None else []
        kwargs = kwargs or {}
        return self.task.apply_async(args=[self.instance] + args, kwargs=kwargs, **options)

    def __getattr__(self, name):
        """Proxy any other attributes to the underlying task."""
        return getattr(self.task, name)

def register_task_with_context(task_result):
    """
    Register a task result with the current RunInCelery context if one exists.
    This is useful for tasks dispatched outside of the enhanced decorators.
    """
    context = RunInCelery.get_current_context()
    if context is not None:
        context.add_dispatched_result(task_result)
    return task_result



# Enhanced TaskMethodDescriptor
class EnhancedTaskMethodDescriptor:
    """
    Enhanced version of TaskMethodDescriptor that uses EnhancedBoundTaskMethod.
    """

    def __init__(self, task):
        self.task = task

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return EnhancedBoundTaskMethod(instance, self.task)

    def __call__(self, *args, **kwargs):
        """Handle direct calls on class-level access."""
        context = RunInCelery.get_current_context()

        if context is None:
            # No context - run synchronously
            return self.task(*args, **kwargs)

        # Check if this task should be dispatched
        task_name = getattr(self.task, 'name', self.task.__name__)

        if context.should_dispatch(task_name):
            # Dispatch asynchronously
            logger.debug(f"Dispatching task {task_name} to Celery")
            result = self.task.delay(*args, **kwargs)
            context.add_dispatched_result(result)
            return result
        else:
            # Run synchronously
            logger.debug(f"Running task {task_name} synchronously")
            return self.task(*args, **kwargs)

    def __getattr__(self, name):
        """Proxy attribute access to the underlying task."""
        return getattr(self.task, name)


# Updated lex_shared_task decorator
def lex_shared_task(_func=None, **task_opts):
    """
    Enhanced version of lex_shared_task that works with RunInCelery context.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                model_context = kwargs.get("model_context", None)
                context = kwargs.get("context", None)
                if context:
                    kwargs.pop('context')
                if model_context:
                    kwargs.pop('model_context')

                # Use your existing CeleryCalculationContext
                with CeleryCalculationContext(context, model_context):

                    result = func(*args, **kwargs)

                return result, args
            except Exception as e:
                logger.error(
                    f"Task {func.__name__} failed with args {args}, kwargs {kwargs}: {e}",
                    exc_info=True
                )
                raise

        options = {
            'base': CallbackTask,
            'bind': False,
        }
        options.update(task_opts)

        celery_task = shared_task(**options)(wrapper)
        celery_task.original_func = func

        # Use the enhanced descriptor
        return EnhancedTaskMethodDescriptor(celery_task)

    if _func is not None and callable(_func):
        return decorator(_func)
    else:
        return decorator



@lex_shared_task
def calc_and_save(models: List[Model], *args, **kwargs):
    """
    Calculates and saves a list of models with robust error handling and
    conflict resolution.

    Args:
        models: A list of model instances to process.
        *args: Additional arguments to pass to the model's calculate() method.
    """
    # Initialize counters for a useful summary
    summary = {
        "total_models": len(models),
        "processed_successfully": 0,
        "conflicts_resolved": 0,
        "errors": 0
    }

    for model in models:
        try:
            with model_logging_context(model):
                logger.info(f"Processing model {model}")
                model.calculate.original_func()
                logger.info(f"Finished calculating model {model}")

                # --- Initial Save Attempt ---
                model.save()
                logger.info(f"Model saved: {model}")
                summary["processed_successfully"] += 1

        except Exception as e:
            try:
                with model_logging_context(model):
                    # --- Conflict Resolution Logic ---
                    logger.warning(f"Integrity error for {model}, attempting conflict resolution.")

                    def save_and_check():
                        old_model = model.delete_models_with_same_defining_fields()
                        model.pk = old_model.pk
                        model.save()

                    save_and_check()

                    logger.info(f"Successfully resolved conflict and saved model {model}")
                    summary["conflicts_resolved"] += 1
                    summary["processed_successfully"] += 1

            except Exception as resolution_error:
                logger.error(f"Conflict resolution FAILED for model {model}: {resolution_error}")
                summary["errors"] += 1
                # If resolution fails, re-raise the error to mark the task as failed
                raise resolution_error


    logger.info(f"Task finished. Summary: {summary}")
    return summary

# Convenience function for backward compatibility
def get_calc_and_save_task():
    """
    Get the calc_and_save task for use in other modules.

    Returns:
        The calc_and_save Celery task
    """
    return calc_and_save


# Export the task for use in other modules
__all__ = ['lex_shared_task', 'CallbackTask', 'calc_and_save', 'get_calc_and_save_task']