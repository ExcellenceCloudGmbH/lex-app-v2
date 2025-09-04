from django.db import models
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE

from lex.lex_app.rest_api.context import context_id


class LexModel(LifecycleModel):
    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    @hook(AFTER_UPDATE)
    def update_edited_by(self):
        try:
            context = context_id.get()
            if context and 'request_obj' in context and hasattr(context['request_obj'], 'auth'):
                self.edited_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
            elif context and context.get('celery_task'):
                # Running in Celery task
                self.edited_by = 'Celery Background Task'
            else:
                self.edited_by = 'Initial Data Upload'
        except Exception:
            # Fallback if context access fails
            self.edited_by = 'System Process'

    @hook(AFTER_CREATE)
    def update_created_by(self):
        try:
            context = context_id.get()
            if context and 'request_obj' in context and hasattr(context['request_obj'], 'auth'):
                self.created_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
            elif context and context.get('celery_task'):
                # Running in Celery task
                self.created_by = 'Celery Background Task'
            else:
                self.created_by = 'Initial Data Upload'
        except Exception:
            # Fallback if context access fails
            self.created_by = 'System Process'


    def track(self):
        del self.skip_history_when_saving


    def untrack(self):
        self.skip_history_when_saving = True

