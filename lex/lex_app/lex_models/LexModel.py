from django.db import models
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE

from lex.lex_app.rest_api.context import operation_context

class LexModel(LifecycleModel):
    
    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    @hook(AFTER_UPDATE)
    def update_edited_by(self):
        context = operation_context.get()
        if context and hasattr(context['request_obj'], 'auth'):
            self.edited_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
        else:
            self.edited_by = 'Initial Data Upload'

    @hook(AFTER_CREATE)
    def update_created_by(self):
        context = operation_context.get()
        if context and hasattr(context['request_obj'], 'auth'):
            self.created_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
        else:
            self.created_by = 'Initial Data Upload'


    def track(self):
        del self.skip_history_when_saving


    def untrack(self):
        self.skip_history_when_saving = True

