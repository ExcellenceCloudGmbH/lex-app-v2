import contextvars
from uuid import uuid4

# Define a context variable
context_id = contextvars.ContextVar('context_id', default={'context_id': '', 'request_obj': '', 'calculation_id': ''})

# Context manager to set operation id
class OperationContext:
    
    def __init__(self, request, calculation_id=None):
        self.request = request
        self.calculation_id = calculation_id
    def __enter__(self):
        # Set a new operation id if one doesn't already exist
        if not context_id.get()['context_id']:
            context_id.set({'context_id': str(uuid4()),
                            'request_obj': self.request,
                            'calculation_id': self.calculation_id})
        return context_id.get()

    def get_request(self):
        return context_id.get()['request_obj']

    def get_calc_id(self):
        return context_id.get()['calculation_id']

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Optionally, reset or clear the operation id here if necessary
        pass