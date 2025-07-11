from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from lex.lex_app.lex_models.UpdateModel import UpdateModel

from lex.lex_app.rest_api.calculated_model_updates.update_handler import (
    CalculatedModelUpdateHandler,
)

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from lex.lex_app.lex_models.Profile import Profile


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    # ensures profile.save() runs even on updates
    instance.profile.save()


def update_calculation_status(instance):
    from lex.lex_app.lex_models.CalculationModel import CalculationModel

    if issubclass(instance.__class__, CalculationModel) or issubclass(
        instance.__class__, UpdateModel
    ):
        channel_layer = get_channel_layer()
        message_type = ""
        if instance.is_calculated == CalculationModel.IN_PROGRESS:
            message_type = "calculation_in_progress"
        elif instance.is_calculated == CalculationModel.SUCCESS:
            message_type = "calculation_success"
        elif instance.is_calculated == CalculationModel.ERROR:
            message_type = "calculation_error"

        message = {
            "type": message_type,  # This is the correct naming convention
            "payload": {
                "record": str(instance),
                "record_id": f"{instance._meta.model_name}_{instance.id}",
            },
        }
        # notification = Notifications(message="Calculation is finished", timestamp=datetime.now())
        # notification.save()
        async_to_sync(channel_layer.group_send)(f"update_calculation_status", message)


def do_post_save(sender, **kwargs):
    CalculatedModelUpdateHandler.register_save(kwargs["instance"])


from django.dispatch import Signal

custom_post_save = Signal()
