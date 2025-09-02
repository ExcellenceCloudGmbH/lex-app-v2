"""
Celery task infrastructure with custom decorators and callback handling.

This module provides enhanced Celery task integration with proper lifecycle
management, status tracking, and error handling for calculation models.
"""

import logging
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple

from celery import Task, shared_task
from django.db import IntegrityError, transaction
from django.db.models import Model

from lex.lex_app import settings
from lex.lex_app.lex_models.CalculationModel import CalculationModel
from lex.lex_app.rest_api.signals import update_calculation_status
from lex.lex_app.rest_api.context import context_id


logger = logging.getLogger(__name__)


class CeleryCalculationContext:
    """
    Context manager to set calculation_id for Celery tasks.
    
    This allows CalculationLog.log() to access the calculation_id
    even when running in a Celery worker process.
    """
    
    def __init__(self, calculation_id):
        self.calculation_id = calculation_id
        self.original_context = None
    
    def __enter__(self):
        if self.calculation_id:
            # Store original context if it exists
            try:
                self.original_context = context_id.get()
            except Exception:
                self.original_context = None
            
            # Set new context with calculation_id and celery marker
            new_context = {
                "calculation_id": self.calculation_id,
                "celery_task": True,  # Marker to indicate this is running in Celery
                "task_name": "calc_and_save"  # Optional: task identification
            }
            context_id.set(new_context)
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.calculation_id:
            # Restore original context
            if self.original_context:
                context_id.set(self.original_context)
            else:
                try:
                    context_id.set({})
                except Exception:
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
                
                # Notify connected systems via WebSocket
                update_calculation_status(model_instance)
                
        except Exception as update_error:
            logger.error(
                f"Failed to update model status for {model_instance}: {update_error}",
                exc_info=True
            )


def custom_shared_task(func):
    """
    Enhanced shared task decorator with proper callback integration.
    
    Wraps functions with Celery task registration and callback handling,
    ensuring proper task lifecycle management and error handling.
    
    Args:
        func: Function to be decorated as a Celery task
        
    Returns:
        Decorated function with Celery task capabilities
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        """
        Task wrapper that executes the original function and returns
        both the result and original arguments for callback processing.
        """
        try:
            result = func(*args, **kwargs)
            # Return both result and args for callback access to model instances
            return result, args
        except Exception as e:
            logger.error(
                f"Task {func.__name__} failed with args {args}, kwargs {kwargs}: {e}",
                exc_info=True
            )
            raise
    
    # Register as Celery shared task with CallbackTask base class
    return shared_task(base=CallbackTask, bind=True)(wrapper)


@custom_shared_task
def calc_and_save(self, models: List[Model], *args, calculation_id=None) -> List[Model]:
    """
    Execute calculations for a group of models with proper error handling.
    
    This is the main calculation task that processes a list of models,
    handles save conflicts using delete_models_with_same_defining_fields,
    and provides detailed error logging.
    
    Args:
        self: Task instance (bound by decorator)
        models: List of model instances to calculate and save
        *args: Additional arguments passed to the calculate method
        calculation_id: Optional calculation ID for logging context
        
    Returns:
        List of successfully processed models
        
    Raises:
        Exception: If any model calculation fails
    """
    processed_models = []
    
    try:
        logger.info(f"Starting calculation for {len(models)} models with calculation_id: {calculation_id}")
        
        # Set up calculation context for logging
        with CeleryCalculationContext(calculation_id):
            for i, model in enumerate(models):
                try:
                    # Execute model calculation with calculation_id context available
                    model.update(*args)
                    
                    # Attempt to save the model
                    try:
                        model.save()
                        processed_models.append(model)
                        logger.debug(f"Successfully processed model {i+1}/{len(models)}: {model}")

                    except IntegrityError as integrity_error:
                        # Handle unique constraint violations using existing method
                        logger.warning(
                            f"Integrity error for model {model}, attempting conflict resolution: {integrity_error}"
                        )

                        if hasattr(model, 'delete_models_with_same_defining_fields'):
                            old_model = model.delete_models_with_same_defining_fields()
                            model.pk = old_model.pk
                            model.save()
                            processed_models.append(model)
                            logger.info(f"Resolved conflict for model {model}")
                        else:
                            logger.error(f"Model {model} does not support conflict resolution")
                            raise
                            
                except Exception as model_error:
                    # Log individual model calculation errors
                    logger.error(
                        f"Calculation failed for model {i+1}/{len(models)} ({model}): {model_error}",
                        exc_info=True
                    )
                    
                    # Update model status to ERROR if it's a CalculationModel
                    if isinstance(model, CalculationModel):
                        model.is_calculated = CalculationModel.ERROR
                        if hasattr(model, 'error_message'):
                            model.error_message = str(model_error)
                        try:
                            model.save(skip_hooks=True)
                        except Exception as save_error:
                            logger.error(f"Failed to save error status for {model}: {save_error}")
                    
                    # Re-raise to trigger task failure
                    raise model_error
                
        logger.info(f"Successfully completed calculation for {len(processed_models)} models")
        return processed_models
        
    except Exception as batch_error:
        # Handle batch-level errors
        logger.error(
            f"Batch calculation failed for task {self.request.id}: {batch_error}",
            exc_info=True
        )
        raise


# Convenience function for backward compatibility
def get_calc_and_save_task():
    """
    Get the calc_and_save task for use in other modules.
    
    Returns:
        The calc_and_save Celery task
    """
    return calc_and_save


# Export the task for use in other modules
__all__ = ['custom_shared_task', 'CallbackTask', 'calc_and_save', 'get_calc_and_save_task']