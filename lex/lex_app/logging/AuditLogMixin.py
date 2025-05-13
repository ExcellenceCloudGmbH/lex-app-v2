import traceback
from lex.lex_app.logging.AuditLog import AuditLog
from lex.lex_app.logging.AuditLogStatus import (
    AuditLogStatus,
)  # Adjust the import path as needed
from lex.lex_app.logging.AuditLogMixinSerializer import _serialize_payload


class AuditLogMixin:
    def log_change(self, action, target, payload=None):
        payload = _serialize_payload(payload or {})
        user = self.request.user if hasattr(self.request, "user") else None
        resource = (
            target.__name__.lower()
            if isinstance(target, type)
            else target.__class__.__name__.lower()
        )
        audit_log = AuditLog.objects.create(
            author=f"{str(user)} ({user.username})" if user else None,
            resource=resource,
            action=action,
            payload=payload,
            calculation_id=self.kwargs.get("calculationId"),
        )
        AuditLogStatus.objects.create(auditlog=audit_log, status="pending")
        return audit_log

    def perform_create(self, serializer):
        payload = _serialize_payload(serializer.validated_data)
        audit_log = self.log_change("create", serializer.Meta.model, payload=payload)
        try:
            instance = serializer.save()
            AuditLogStatus.objects.filter(auditlog=audit_log).update(status="success")
            return instance
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(auditlog=audit_log).update(
                status="failure", error_traceback=error_msg
            )
            raise e

    def perform_update(self, serializer):
        initial_payload = _serialize_payload(serializer.validated_data)
        audit_log = self.log_change(
            "update", serializer.Meta.model, payload=initial_payload
        )
        try:
            instance = serializer.save()
            updated_payload = _serialize_payload(self.get_serializer(instance).data)
            audit_log.payload = updated_payload
            audit_log.save(update_fields=["payload"])
            AuditLogStatus.objects.filter(auditlog=audit_log).update(status="success")
            return instance
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(auditlog=audit_log).update(
                status="failure", error_traceback=error_msg
            )
            raise e

    def perform_destroy(self, instance):
        serializer = self.get_serializer(instance)
        payload = _serialize_payload(serializer.data)
        audit_log = self.log_change("delete", instance, payload=payload)
        try:
            instance.delete()
            AuditLogStatus.objects.filter(auditlog=audit_log).update(status="success")
        except Exception as e:
            error_msg = traceback.format_exc()
            AuditLogStatus.objects.filter(auditlog=audit_log).update(
                status="failure", error_traceback=error_msg
            )
            raise e
