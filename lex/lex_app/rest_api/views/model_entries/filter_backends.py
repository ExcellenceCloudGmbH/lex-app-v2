import base64
from urllib.parse import parse_qs
from rest_framework import filters
from lex.lex_app.logging.CalculationLog import CalculationLog

# KeycloakManager is no longer needed here as permissions come from middleware


class PrimaryKeyListFilterBackend(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        model_container = view.kwargs['model_container']

        if 'ids' in request.query_params.dict():
            ids = {**request.query_params}['ids']
            ids_cleaned = list(filter(lambda x: x != '', ids))
            filter_arguments = {
                f'{model_container.pk_name}__in': ids_cleaned
            }
        else:
            filter_arguments = {}
        return queryset.filter(**filter_arguments)

    def filter_for_export(self, json_data, queryset, view):
        model_container = view.kwargs['model_container']
        decoded = base64.b64decode(json_data["filtered_export"]).decode("utf-8")
        params = parse_qs(decoded)
        if 'ids' in dict(params):
            ids = dict(params)['ids']
            ids_cleaned = list(filter(lambda x: x != '', ids))
            filter_arguments = {
                f'{model_container.pk_name}__in': ids_cleaned
            }
        else:
            filter_arguments = {}
        return queryset.filter(**filter_arguments)


class UserReadRestrictionFilterBackend(filters.BaseFilterBackend):
    """
    Filter to handle record-level visibility based on 'read' scope.

    For CalculationLog, delegates permission check to linked CalculationModel instance.
    """

    def filter_queryset(self, request, queryset, view):
        permitted_pks = []
        model_class = view.kwargs['model_container'].model_class


        if model_class.__name__ == 'AuditLogStatus':
            for instance in queryset:
                try:
                    audit_log = instance.auditlog

                    related_obj = getattr(audit_log, 'calculatable_object', None)
                    # If there's a linked CalculationModel with can_read
                    if related_obj and hasattr(related_obj, 'can_read'):
                        if related_obj.can_read(request):
                            permitted_pks.append(instance.pk)
                    else:
                        # Optional: fallback policy (allow or deny if no related obj)
                        # For safety, probably deny by default
                        permitted_pks.append(instance.pk)
                        continue
                except Exception as e:
                    permitted_pks.append(instance.pk)
            return queryset.filter(pk__in=permitted_pks)



        if model_class.__name__ == 'AuditLog':
            # For each CalculationLog instance, check permission on linked calculatable_object
            for instance in queryset:
                try:
                    related_obj = getattr(instance, 'calculatable_object', None)
                    # If there's a linked CalculationModel with can_read
                    if related_obj and hasattr(related_obj, 'can_read'):
                        if related_obj.can_read(request):
                            permitted_pks.append(instance.pk)
                    else:
                        # Optional: fallback policy (allow or deny if no related obj)
                        # For safety, probably deny by default
                        permitted_pks.append(instance.pk)
                        continue
                except Exception as e:
                    permitted_pks.append(instance.pk)
            return queryset.filter(pk__in=permitted_pks)

        # Special handling for CalculationLog model (or subclass)
        if model_class.__name__ == 'CalculationLog':
            # For each CalculationLog instance, check permission on linked calculatable_object
            for instance in queryset:
                related_obj = getattr(instance, 'calculatable_object', None)
                # If there's a linked CalculationModel with can_read
                if related_obj and hasattr(related_obj, 'can_read'):
                    if related_obj.can_read(request):
                        permitted_pks.append(instance.pk)
                else:
                    # Optional: fallback policy (allow or deny if no related obj)
                    # For safety, probably deny by default
                    permitted_pks.append(instance.pk)
                    continue
            return queryset.filter(pk__in=permitted_pks)

        # Default behavior for LexModel subclasses
        for instance in queryset:
            if hasattr(instance, 'can_read'):
                if callable(getattr(instance, 'can_read')):
                    visible_fields = instance.can_read(request)
                    if visible_fields:
                        permitted_pks.append(instance.pk)
            else:
                permitted_pks.append(instance.pk)
        return queryset.filter(pk__in=permitted_pks)
