import contextvars
from typing import Dict, Any
from uuid import uuid4

# Define a context variable with meaningful name and proper type annotation
operation_context: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    'operation_context', 
    default={'operation_id': '', 'request_obj': '', 'calculation_id': '', 'audit_log_temp': None}
)

# Context manager to set operation id
class OperationContext:
    
    def __init__(self, request, calculation_id=None, audit_log=None):
        self.request = request
        self.calculation_id = calculation_id
        self.audit_log = audit_log
    def __enter__(self):
        # Set a new operation id if one doesn't already exist
        if not operation_context.get()['operation_id']:
            operation_context.set({'operation_id': str(uuid4()),
                            'request_obj': self.request,
                            'calculation_id': self.calculation_id, 'audit_log_temp': self.audit_log})
        return operation_context.get()

    def get_request(self):
        return context_id.get()['request_obj']

    def get_calc_id(self):
        return context_id.get()['calculation_id']

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Optionally, reset or clear the operation id here if necessary
        operation_context.set(
            {'operation_id': '', 'request_obj': '', 'calculation_id': '', 'audit_log_temp': None}
        )