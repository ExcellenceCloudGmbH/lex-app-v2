import json

from django.core.management.base import BaseCommand
from django.apps import apps
from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from keycloak.exceptions import KeycloakPostError, KeycloakGetError, KeycloakDeleteError

from lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager


class Command(BaseCommand):
    help = (
        "Register each Django model as a Keycloak UMA resource and "
        "wire up resource-based permissions based on three main policies: admin, standard, and view-only."
    )

    def handle(self, *args, **options):
        # 1) Connect to Keycloak using client credentials

        KeycloakManager().setup_django_model_permissions_resource_based()