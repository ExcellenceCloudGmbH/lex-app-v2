import base64
from urllib.parse import parse_qs
from rest_framework import filters

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
    Refactored to handle record-level visibility based on the 'read' scope.

    This filter performs the first, broad-phase step of authorization for list views.
    It efficiently filters the queryset at the database level to include only the
    records for which the user has a specific record-level 'read' permission.

    The final determination of which fields are visible (and whether a record is
    ultimately shown) is handled by the `PermissionAwareModelSerializer`.
    """
    def filter_queryset(self, request, queryset, view):
        # model_class = view.kwargs['model_container'].model_class
        # resource_name = f"{model_class._meta.app_label}.{model_class.__name__}"

        # Get the permissions list cached on the request by the middleware.
        permitted_pks = []
        for instance in queryset:
            # For each instance, call the can_read method.
            visible_fields = instance.can_read(request)
            # If the method returns a non-empty set, the user can see the record.
            if visible_fields:
                permitted_pks.append(instance.pk)

        # Return a new queryset containing only the records the user is allowed to see.
        return queryset.filter(pk__in=permitted_pks)
