from django.core.management.base import BaseCommand
from django.apps import apps
from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from keycloak.exceptions import KeycloakDeleteError, KeycloakGetError

from keycloak.urls_patterns import (
    URL_ADMIN_CLIENT_AUTHZ_SCOPE_PERMISSION,
)




class Command(BaseCommand):
    help = "Deletes all UMA resources, permissions, policies, and roles created by the setup script."

    def handle(self, *args, **options):
        # 1) Connect to Keycloak
        try:
            self.stdout.write("Connecting to Keycloak...")
            conn = KeycloakOpenIDConnection(
                server_url="https://exc-testing.com",
                realm_name="lex",
                client_id="LEX_LOCAL_ENV",
                client_secret_key="IYT2HQyuPuoKN3ff73eZUdJc29YWyET5",
                verify=False,
            )
            kc_uma = KeycloakUMA(connection=conn)
            kc_admin = KeycloakAdmin(connection=conn)
            self.stdout.write(self.style.SUCCESS("✔ Connected successfully."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to connect to Keycloak: {e}"))
            return

        # 2) Your client's internal UUID
        client_uuid = "3e5eeafe-a3b3-469e-9db3-54cff7108d70"

        self.stdout.write("Starting rollback process...")

        # 3) Iterate over all Django models to delete associated permissions and resources
        scopes = ["list", "show", "create", "edit", "delete", "export"]
        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            self.stdout.write("---")
            self.stdout.write(f"Processing Model: {res_name}")

            # --- Delete permissions for each scope ---
            for scope in scopes:
                perm_name = f"Permission: {res_name}:{scope}"
                try:
                    permissions = kc_admin.get_client_authz_permissions(client_id=client_uuid)

                    if permissions:
                        perm_id = permissions[0]['id']
                        kc_admin.raw_delete(URL_ADMIN_CLIENT_AUTHZ_SCOPE_PERMISSION.format(
                            **{"realm-name":"lex", "id":client_uuid, "scope-id":perm_id}
                        ))

                        self.stdout.write(self.style.SUCCESS(f"    🗑 Deleted permission: {perm_name}"))
                    else:
                        self.stdout.write(f"    - Permission not found, skipping: {perm_name}")
                except (KeycloakDeleteError, KeycloakGetError) as e:
                    self.stderr.write(self.style.WARNING(f"    Could not delete permission {perm_name}: {e}"))

            # --- Delete the UMA resource for the model ---
            resources = kc_uma.resource_set_list()
            for resource in resources:
                try:
                    resource_id = resource['_id']
                    kc_uma.resource_set_delete(resource_id)
                except (KeycloakDeleteError, KeycloakGetError) as e:
                    self.stderr.write(self.style.WARNING(f"  Could not delete UMA resource {resource}: {e}"))

        # 4) Delete the three core policies and their associated roles
        self.stdout.write("\n---")
        self.stdout.write("Deleting core policies and roles...")
        policy_names = ["admin", "standard", "view-only"]
        for name in policy_names:
            policy_name = f"Policy: {name}"

            # a) Delete Role Policy
            try:
                policies = kc_admin.get_client_authz_policies(client_id=client_uuid)
                if policies:
                    policy_id = policies[0]['id']
                    kc_admin.delete_client_authz_policy(client_id=client_uuid, policy_id=policy_id)
                    self.stdout.write(self.style.SUCCESS(f"  🗑 Deleted policy: {policy_name}"))
                else:
                    self.stdout.write(f"  - Policy not found, skipping: {policy_name}")
            except (KeycloakDeleteError, KeycloakGetError) as e:
                self.stderr.write(self.style.WARNING(f"  Could not delete policy {policy_name}: {e}"))


        self.stdout.write("\n---")
        self.stdout.write(self.style.SUCCESS("Keycloak authorization rollback complete."))
