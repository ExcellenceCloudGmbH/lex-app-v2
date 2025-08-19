"""
Context resolver for the improved CalculationLog system.

This module provides the ContextResolver class that integrates the dual context
system (operation context and model context) into a unified ContextInfo object
for use in calculation logging operations.
"""

import logging
from typing import Optional

from django.contrib.contenttypes.models import ContentType

from lex.lex_app.rest_api.context import context_id
from lex.lex_app.logging.model_context import _model_stack
from lex.lex_app.logging.AuditLog import AuditLog
from lex.lex_app.logging.data_models import ContextInfo, ContextResolutionError

logger = logging.getLogger(__name__)


class ContextResolver:
    """
    Resolves and integrates dual context systems for calculation logging.
    
    This class combines operation context (from context_id) and model context
    (from _model_stack) into a unified ContextInfo object that can be used
    throughout the calculation logging system.
    """
    
    @staticmethod
    def resolve() -> ContextInfo:
        """
        Resolve current context by integrating both context systems.
        
        Extracts:
        - Operation context from context_id (calculation_id, request_obj)
        - Model context from _model_stack (current/parent models)
        
        Returns:
            ContextInfo: Unified context information for logging operations
            
        Raises:
            ContextResolutionError: When required context information is missing
                or when AuditLog cannot be resolved
        """
        try:
            # Extract calculation_id from operation context
            context_data = context_id.get()
            calculation_id = context_data.get('calculation_id')
            
            if not calculation_id:
                raise ContextResolutionError(
                    "Missing calculation_id in operation context",
                    calculation_id=calculation_id
                )
            
            # Resolve AuditLog using calculation_id
            try:
                audit_log = AuditLog.objects.get(calculation_id=calculation_id)
            except AuditLog.DoesNotExist:
                raise ContextResolutionError(
                    f"AuditLog not found for calculation_id: {calculation_id}",
                    calculation_id=calculation_id
                )
            except Exception as e:
                raise ContextResolutionError(
                    f"Error retrieving AuditLog for calculation_id {calculation_id}: {str(e)}",
                    calculation_id=calculation_id
                )
            
            # Extract model stack from model context
            stack = _model_stack.get()
            stack_length = len(stack) if stack else 0
            
            # Determine current and parent models
            current_model = None
            parent_model = None
            current_record = None
            parent_record = None
            content_type = None
            parent_content_type = None
            
            if stack_length > 0:
                current_model = stack[-1]
                if current_model:
                    try:
                        content_type = ContentType.objects.get_for_model(current_model)
                        current_record = f"{current_model._meta.model_name}_{current_model.pk}"
                    except Exception as e:
                        logger.warning(
                            f"Error resolving ContentType for current model: {e}",
                            extra={'calculation_id': calculation_id}
                        )
            
            if stack_length > 1:
                parent_model = stack[-2]
                if parent_model:
                    try:
                        parent_content_type = ContentType.objects.get_for_model(parent_model)
                        parent_record = f"{parent_model._meta.model_name}_{parent_model.pk}"
                    except Exception as e:
                        logger.warning(
                            f"Error resolving ContentType for parent model: {e}",
                            extra={'calculation_id': calculation_id}
                        )
            
            # Create and return unified ContextInfo
            context_info = ContextInfo(
                calculation_id=calculation_id,
                audit_log=audit_log,
                current_model=current_model,
                parent_model=parent_model,
                current_record=current_record,
                parent_record=parent_record,
                content_type=content_type,
                parent_content_type=parent_content_type
            )
            
            logger.debug(
                f"Context resolved successfully for calculation_id: {calculation_id}",
                extra={
                    'calculation_id': calculation_id,
                    'stack_length': stack_length,
                    'has_current_model': current_model is not None,
                    'has_parent_model': parent_model is not None
                }
            )
            
            return context_info
            
        except ContextResolutionError:
            # Re-raise ContextResolutionError as-is
            raise
        except Exception as e:
            # Wrap any other exceptions in ContextResolutionError
            calculation_id = None
            try:
                calculation_id = context_id.get().get('calculation_id')
            except Exception:
                pass
            
            raise ContextResolutionError(
                f"Unexpected error during context resolution: {str(e)}",
                calculation_id=calculation_id,
                stack_length=len(_model_stack.get()) if _model_stack.get() else 0
            )