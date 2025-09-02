from datetime import datetime
import logging

from django.db import models

from lex.lex_app.lex_models.ModificationRestrictedModelExample import (
    AdminReportsModificationRestriction,
)
from lex.lex_app.rest_api.context import context_id
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from lex.lex_app.logging.AuditLog import AuditLog
from lex.lex_app.logging.model_context import _model_stack
from django.core.cache import caches
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

#### Note: Messages shall be delivered in the following format: "Severity: Message" The colon and the whitespace after are required for the code to work correctly ####
# Severity could be something like 'Error', 'Warning', 'Caution', etc. (See Static variables below!)


class CalculationLog(models.Model):
    modification_restriction = AdminReportsModificationRestriction()
    id = models.AutoField(primary_key=True)
    timestamp = models.DateTimeField(default=datetime.now())
    calculationId = models.TextField(default="test_id")
    calculation_log = models.TextField(default="")
    calculationlog = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="parent_logs",
    )  # parent calculation log
    auditlog = models.ForeignKey(
        "AuditLog", on_delete=models.CASCADE, null=True, blank=True
    )
    # Generic fields to reference any calculatable object:
    # If you want to allow CalculationLog entries without a related instance,
    # consider setting null=True and blank=True. Otherwise, ensure an instance is always found.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    # This generic foreign key ties the above two fields, allowing dynamic reference.
    calculatable_object = GenericForeignKey("content_type", "object_id")

    # Severities – to be concatenated with the message in the create statement
    SUCCESS = "Success: "
    WARNING = "Warning: "
    ERROR = "Error: "
    START = "Start: "
    FINISH = "Finish: "

    # Message types
    PROGRESS = "Progress"
    INPUT = "Input Validation"
    OUTPUT = "Output Validation"

    class Meta:
        app_label = "lex_app"

    @classmethod
    def log(cls, message: str):
        """
        Logs `message` against the current model context:
        - current_model  = stack[-1]
        - parent_model   = stack[-2] (if present)
        """
        # 1) Grab the model stack
        stack = _model_stack.get()
        # if not stack:
        #     logging.getLogger("lex.calclog") \
        #         .warning("No model context for log: %s", message)
        #     return
        # 2) Resolve calculation_id & AuditLog
        calc_id = context_id.get()["calculation_id"]
        print("Cacluation ID:", calc_id)
        redis_cache = caches["redis"]
        audit_log = AuditLog.objects.get(calculation_id=calc_id)
        channel_layer = get_channel_layer()

        if len(stack) > 0:
            current_model = stack[-1]
            current_model_pk = current_model.pk
            # 3) Prepare CT and record string for current
            ctype_cur = ContentType.objects.get_for_model(current_model)
            current_record = f"{current_model._meta.model_name}_{current_model.pk}"

            calc_id_message = {
                "type": "calculation_id",
                "payload": {
                    "calculation_record": current_record,
                    "calculation_id": calc_id,
                },
            }
            async_to_sync(channel_layer.group_send)("calculations", calc_id_message)
        else:
            current_model = None
            current_model_pk = None
            ctype_cur = None
            current_record = None

        if len(stack) > 1:
            parent_model = stack[-2]
            if parent_model:
                ctype_par = ContentType.objects.get_for_model(parent_model)
                parent_record = f"{parent_model._meta.model_name}_{parent_model.pk}"

                parent_log, _ = cls.objects.get_or_create(
                    calculationId=calc_id,
                    auditlog=audit_log,
                    content_type=ctype_par,
                    object_id=parent_model.pk,
                )

                calc_id_message = {
                    "type": "calculation_id",
                    "payload": {
                        "calculation_record": parent_record,
                        "calculation_id": calc_id,
                    },
                }
                async_to_sync(channel_layer.group_send)("calculations", calc_id_message)
        else:
            parent_log = None

        # 4) If we have a parent, ensure its log exists first

        log_entry, _ = cls.objects.get_or_create(
            calculationId=calc_id,
            auditlog=audit_log,
            content_type=ctype_cur,
            object_id=current_model_pk,
            calculationlog=parent_log,
        )

        # 5) Append & save
        log_entry.calculation_log = (log_entry.calculation_log or "") + f"\n{message}"
        log_entry.save()

        redis_cache.set(
            f"{current_record}_{calc_id}",
            redis_cache.get(f"{current_record}_{calc_id}", "") + "\n" + message,
            timeout=60 * 60 * 24 * 7,  # Cache for one week
        )
        # 6) Also emit to your WebSocket/logger
        logging.getLogger("lex.calclog").debug(
            message,
            extra={
                "calculation_record": current_record,
                "calculationId": calc_id,
            },
        )
