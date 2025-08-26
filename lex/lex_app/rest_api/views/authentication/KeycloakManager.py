import json
import logging
from urllib.parse import urlencode

from django.apps.registry import apps
from django.conf import settings
from keycloak import KeycloakAdmin, KeycloakOpenID, KeycloakOpenIDConnection, KeycloakUMA
from keycloak.exceptions import KeycloakPostError, KeycloakGetError
from lex.lex_app.decorators.LexSingleton import LexSingleton

# It's good practice to have a dedicated logger
logger = logging.getLogger(__name__)


@LexSingleton
class KeycloakManager:
    """
    A centralized client for managing Keycloak interactions, including both:
    1. Admin operations (managing users, permissions) via the Admin API.
    2. OIDC client operations (token refresh, UMA permissions) for end-users.

    Configuration is pulled from Django's settings.py file.
    Requires `python-keycloak` to be installed.
    """

    def __init__(self):
        """
        Initializes both the Keycloak Admin client and the OpenID Connect client.
        """
        self.realm_name = None
        self.client_uuid = None
        self.oidc = None
        self.admin = None
        self.conn = None
        self.uma = None

        self.initialize()

    def initialize(self):
        self.realm_name = settings.KEYCLOAK_REALM_NAME
        self.client_uuid = settings.OIDC_RP_CLIENT_UUID

        # Get SSL verification setting from Django settings
        verify_ssl = getattr(settings, 'KEYCLOAK_VERIFY_SSL', False)

        try:
            # When initializing the OIDC client, ensure proper scopes
            # self.conn = KeycloakOpenIDConnection(
            #     server_url=settings.KEYCLOAK_URL,
            #     username='technical_controller',
            #     password='dw0=jjK34mu10)kaio',
            #     user_realm_name='master',
            #     realm_name=self.realm_name,
            #     # client_id=settings.OIDC_RP_CLIENT_ID,
            #     # client_secret_key=settings.OIDC_RP_CLIENT_SECRET,
            #     verify=verify_ssl
            # )
            self.conn= KeycloakOpenIDConnection(
                server_url=settings.KEYCLOAK_URL,
                client_id=settings.OIDC_RP_CLIENT_ID,
                realm_name=self.realm_name,
                client_secret_key=settings.OIDC_RP_CLIENT_SECRET,
                verify=verify_ssl
            )
            self.admin = KeycloakAdmin(connection=self.conn)
            self.uma = KeycloakUMA(connection=self.conn)
            self.oidc = self.conn.keycloak_openid
        except Exception as e:
            logger.error(f"Failed to initialize Keycloak OIDC client: {e}")
            self.oidc = None

    def setup_django_model_permissions_resource_based(self):
        """
        Initializes Keycloak UMA resources using a resource-based permission model.
        It creates a permission with one scope, then updates it with the rest.
        """
        if not self.admin or not self.uma:
            logger.error("Keycloak clients not initialized. Aborting setup.")
            return

        client_uuid = getattr(settings, 'OIDC_RP_CLIENT_UUID', None)
        if not client_uuid:
            logger.error("❌ OIDC_RP_CLIENT_UUID is not configured in settings. Aborting.")
            return

        # --- 1. Pre-load existing Keycloak configurations ---
        logger.info("Loading existing Keycloak configurations...")
        try:
            existing_resources = {r["name"]: r for r in self.uma.resource_set_list()}
            existing_roles = {r["name"]: r for r in self.admin.get_client_roles(client_id=client_uuid)}
            existing_policies = {p["name"]: p for p in self.admin.get_client_authz_policies(client_id=client_uuid)}
            # We get all permissions to check for existence before creating
            existing_permissions = {p["name"]: p for p in
                                    self.admin.get_client_authz_permissions(client_id=client_uuid)}
            logger.info("✔ Configurations loaded.")
        except KeycloakGetError as e:
            logger.error(f"❌ Could not load client configurations: {e.response_body}")
            return

        # --- 2. Define and set up core policies ---
        policy_definitions = {
            "admin": ["list", "show", "create", "edit", "delete", "export"],
            "standard": ["list", "show", "create", "edit", "export"],
            "view-only": ["list", "show"],
        }
        policy_ids = {}

        logger.info("\n--- Setting up core policies ---")
        # (This section for creating roles and policies remains the same as it was working correctly)
        for policy_name in policy_definitions.keys():
            role_id = existing_roles.get(policy_name, {}).get("id")
            if not role_id:
                logger.warning(f"  - Role '{policy_name}' not found. Please ensure it exists.")
                continue

            full_policy_name = f"Policy - {policy_name}"
            policy = existing_policies.get(full_policy_name)
            if policy:
                policy_ids[policy_name] = policy["id"]
                logger.info(f"  ✔ Policy exists: {full_policy_name}")
            else:
                try:
                    roles_config = [{"id": role_id, "required": True}]
                    policy_payload = {
                        "name": full_policy_name, "type": "role", "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS", "roles": roles_config
                    }
                    created_policy = self.admin.create_client_authz_role_based_policy(
                        client_id=client_uuid, payload=policy_payload
                    )
                    policy_ids[policy_name] = created_policy["id"]
                    existing_policies[full_policy_name] = created_policy
                    logger.info(f"  ✨ Created role policy: {full_policy_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create policy {full_policy_name}: {e}")

        # --- 3. Iterate over models to create resources and permissions ---
        all_scopes = ["list", "show", "create", "edit", "delete", "export"]
        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            logger.info(f"\n--- Processing Model: {res_name} ---")

            # a) Create or fetch UMA resource for the model
            resource = existing_resources.get(res_name)
            resource_id = None
            if resource:
                resource_id = resource.get("_id")
                logger.info(f"  ✔ UMA resource exists: {res_name}")
            else:
                try:
                    payload = {"name": res_name, "scopes": [{"name": s} for s in all_scopes]}
                    created = self.uma.resource_set_create(payload)
                    resource_id = created.get("_id")
                    logger.info(f"  ✨ Created UMA resource: {res_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create resource {res_name}: {e}")
                    continue

            if not resource_id:
                logger.error(f"  ❌ Could not get resource ID for {res_name}. Skipping permissions.")
                continue

            # b) **THE EXPERIMENT**: Create ONE resource-based permission per policy, then UPDATE it
            for policy_name, scopes_for_policy in policy_definitions.items():
                if policy_name not in policy_ids or not scopes_for_policy:
                    continue

                policy_id = policy_ids[policy_name]
                perm_name = f"Permission - {res_name} - {policy_name}"

                if perm_name in existing_permissions:
                    logger.info(f"    ✔ Resource permission already exists: {perm_name}")
                    continue

                # 1. Create the permission with ONLY the first scope
                try:
                    initial_payload = {
                        "name": perm_name, "type": "resource", "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS", "resources": [resource_id],
                        "policies": [policy_id],
                        "scopes": [scopes_for_policy[0]],  # CRITICAL: Start with just one scope
                    }
                    created_perm = self.admin.create_client_authz_resource_based_permission(
                        client_id=client_uuid, payload=initial_payload
                    )
                    logger.info(
                        f"    🛡️  Created initial permission '{perm_name}' with scope: '{scopes_for_policy[0]}'")

                    # 2. If there are more scopes, UPDATE the permission
                    if len(scopes_for_policy) > 1:
                        update_payload = created_perm
                        update_payload['scopes'] = scopes_for_policy

                        # **THE FIX**: The correct method is the generic update_client_authz_permission
                        self.admin.update_client_authz_permission(
                            client_id=client_uuid,
                            permission_id=created_perm['id'],
                            payload=update_payload
                        )
                        logger.info(f"    ➕ Updated permission with all scopes: {', '.join(scopes_for_policy)}")

                    existing_permissions[perm_name] = created_perm

                except KeycloakPostError as e:
                    logger.error(f"    ❌ Failed to create/update permission {perm_name}: {e.response_body}")
                except Exception as e:
                    logger.error(f"    ❌ An unexpected error occurred for permission {perm_name}: {e}")

        logger.info("\n✅ Keycloak resource-based setup complete.")

    def retry(self):
        self.initialize()
        return bool(self.conn)

    def setup_django_model_scope_based_permissions(self):
        """
        Initializes Keycloak UMA resources and permissions for all Django models.
        This script uses the recommended SCOPE-BASED permission model.
        """
        if not self.admin or not self.uma:
            logger.error("Keycloak clients not initialized. Aborting setup.")
            return

        client_uuid = getattr(settings, 'OIDC_RP_CLIENT_UUID', None)
        if not client_uuid:
            logger.error("❌ OIDC_RP_CLIENT_UUID is not configured in settings. Aborting.")
            return

        # --- 1. Pre-load existing Keycloak authorization configurations ---
        logger.info("Loading existing Keycloak configurations...")
        try:
            existing_resources = {r["name"]: r for r in self.uma.resource_set_list()}
            existing_roles = {r["name"]: r for r in self.admin.get_client_roles(client_id=client_uuid)}
            existing_policies = {p["name"]: p for p in self.admin.get_client_authz_policies(client_id=client_uuid)}
            existing_permissions = {p["name"]: p for p in
                                    self.admin.get_client_authz_permissions(client_id=client_uuid)}
            logger.info("✔ Configurations loaded.")
        except KeycloakGetError as e:
            logger.error(
                f"\n❌ Could not load client configurations. "
                f"Please check if client UUID '{client_uuid}' is correct and has 'Authorization' enabled in Keycloak."
            )
            logger.error(f"   Keycloak error: {e.response_body}")
            return

        # --- 2. Define core policies and the scopes they grant ---
        policy_definitions = {
            "admin": ["list", "show", "create", "edit", "delete", "export"],
            "standard": ["list", "show", "create", "edit", "export"],
            "view-only": ["list", "show"],
        }
        policy_ids = {}

        logger.info("\n--- Setting up core policies: admin, standard, view-only ---")
        for policy_name in policy_definitions.keys():
            role_id = None
            policy_id = None

            # a) Ensure the client role exists (e.g., 'admin', 'standard')
            if policy_name in existing_roles:
                role_id = existing_roles[policy_name]["id"]
                logger.info(f"  ✔ Client role exists: {policy_name}")
            else:
                try:
                    self.admin.create_client_role(
                        client_role_id=client_uuid,
                        payload={"name": policy_name, "description": f"Role for {policy_name} access"},
                    )
                    role = self.admin.get_client_role(client_id=client_uuid, role_name=policy_name)
                    role_id = role["id"]
                    existing_roles[policy_name] = role
                    logger.info(f"  ✨ Created client role: {policy_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create role {policy_name}: {e}")
                    continue

            # b) Ensure a role-based policy linked to that role exists
            full_policy_name = f"Policy - {policy_name}"
            if full_policy_name in existing_policies:
                policy_id = existing_policies[full_policy_name]["id"]
                logger.info(f"  ✔ Role policy exists: {full_policy_name}")
            else:
                try:
                    roles_config = [{"id": role_id, "required": True}]
                    policy_payload = {
                        "name": full_policy_name,
                        "type": "role",
                        "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS",
                        "roles": roles_config
                    }
                    created_policy = self.admin.create_client_authz_role_based_policy(
                        client_id=client_uuid, payload=policy_payload
                    )
                    policy_id = created_policy["id"]
                    existing_policies[full_policy_name] = created_policy
                    logger.info(f"  ✨ Created role policy: {full_policy_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create policy {full_policy_name}: {e}")
                    if hasattr(e, 'response_body'):
                        logger.error(f"     Response: {e.response_body}")
                    continue

            # Store the successfully created policy ID
            if role_id and policy_id:
                policy_ids[policy_name] = policy_id

        if not policy_ids:
            logger.error("\n❌ No policies were created successfully. Cannot proceed.")
            return

        # --- 3. Iterate over all Django models to create resources and permissions ---
        all_scopes = ["list", "show", "create", "edit", "delete", "export"]
        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            logger.info(f"\n--- Processing Model: {res_name} ---")

            # a) Create or fetch UMA resource for the model
            resource_id = None
            if res_name in existing_resources:
                resource_id = existing_resources[res_name].get("_id")
                logger.info(f"  ✔ UMA resource exists: {res_name}")
            else:
                try:
                    payload = {
                        "name": res_name,
                        "scopes": [{"name": s} for s in all_scopes],
                    }
                    created = self.uma.resource_set_create(payload)
                    resource_id = created.get("_id")
                    existing_resources[res_name] = created
                    logger.info(f"  ✨ Created UMA resource: {res_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create resource {res_name}: {e}")
                    continue

            if not resource_id:
                logger.error(f"  ❌ Could not get resource ID for {res_name}. Skipping permissions.")
                continue

            # b) Create one SCOPE-BASED permission per scope, per policy
            for policy_name, scopes_for_policy in policy_definitions.items():
                if policy_name not in policy_ids:
                    logger.warning(f"    ⏭ Skipping policy {policy_name} - setup failed earlier.")
                    continue

                policy_id = policy_ids[policy_name]

                # Inner loop to create a permission for each individual scope
                for scope in scopes_for_policy:
                    perm_name = f"Permission - {res_name} - {policy_name} - {scope}"

                    if perm_name in existing_permissions:
                        logger.info(f"    ✔ Scope permission exists: {perm_name}")
                        continue

                    permission_payload = {
                        "name": perm_name,
                        "type": "scope",
                        "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS",
                        "resources": [resource_id],  # Link to the resource
                        "scopes": [scope],  # Link to the SINGLE scope
                        "policies": [policy_id],  # Link to the policy
                    }

                    try:
                        # **THE FIX**: The correct method is create_client_authz_scope_permission
                        self.admin.create_client_authz_scope_permission(
                            client_id=client_uuid,
                            payload=permission_payload
                        )
                        existing_permissions[perm_name] = {"name": perm_name}
                        logger.info(f"    🛡  Created scope permission: {perm_name}")
                    except KeycloakPostError as e:
                        logger.error(f"    ❌ Failed to create permission {perm_name}: {e.response_body}")
                    except Exception as e:
                        logger.error(f"    ❌ An unexpected error occurred creating permission {perm_name}: {e}")

        logger.info("\n---")
        logger.info("✅ Keycloak authorization setup complete.")
    def get_uma_permissions(self, access_token: str):
        """
        Fetches UMA (User-Managed Access) permissions for a given access token.
        This encapsulates the logic from your `helpers.py`.

        Args:
            access_token (str): The user's current access token.

        Returns:
            A dict of UMA permissions or None if an error occurs.
        """
        if not self.oidc:
            logger.error("OIDC client not initialized. Cannot fetch UMA permissions.")
            return None

        try:
            return self.oidc.uma_permissions(token=access_token)
        except Exception as e:
            logger.error(f"Failed to fetch UMA permissions: {e}")
            return None

    def refresh_user_token(self, refresh_token: str):
        """
        Refreshes a user's access token using their refresh token.
        This encapsulates the logic from your `RefreshTokenSessionMiddleware`.

        Args:
            refresh_token (str): The user's refresh token.

        Returns:
            A dict with new tokens ('access_token', 'refresh_token', etc.) or None.
        """
        if not self.oidc:
            logger.error("OIDC client not initialized. Cannot refresh token.")
            if not self.retry():
                return None

        try:
            return self.oidc.refresh_token(refresh_token)
        except KeycloakPostError as e:
            logger.warning(f"Failed to refresh token: {e.response_code} - {e.response_body}")
            return None

    def get_user_permissions(self, access_token: str, model_or_instance):
        """
        Gets the allowed actions for a user on a specific Django model or model instance.

        Args:
            access_token (str): The user's access token.
            model_or_instance: A Django model class or an instance of a model.

        Returns:
            A set of allowed scopes (e.g., {'view', 'edit'}).
        """
        if not self.oidc:
            logger.error("OIDC client not initialized.")
            if not self.retry():
                return set()

        try:
            uma_permissions = self.oidc.uma_permissions(token=access_token)

            # Determine the resource name
            if hasattr(model_or_instance, '_meta'):  # It's an instance or a model class
                app_label = model_or_instance._meta.app_label
                model_name = model_or_instance._meta.model_name
                resource_name = f"{app_label}.{model_name}"
            else:
                return set()

            allowed_scopes = set()
            for perm in uma_permissions:
                if perm.get('rsname') == resource_name:
                    # Check for record-specific permissions if an instance is provided
                    if hasattr(model_or_instance, 'pk') and model_or_instance.pk:
                        if perm.get('resource_set_id') == str(model_or_instance.pk):
                            allowed_scopes.update(perm.get('scopes', []))
                    else:  # General model permissions
                        allowed_scopes.update(perm.get('scopes', []))

            return allowed_scopes

        except Exception as e:
            logger.error(f"Failed to get UMA permissions: {e}")
            return set()

    def setup_django_model_permissions(self):
        """
        Initializes Keycloak UMA resources and permissions for all Django models.
        This is a refactoring of your keycloak_init_bak.py script.
        """
        if not self.admin:
            logger.error("Admin client not initialized.")
            if not self.retry():
                return

        # 3) Pre-load existing Keycloak authorization configurations
        logger.info("Loading existing Keycloak configurations...")
        client_uuid = settings.OIDC_RP_CLIENT_UUID or ""
        try:
            existing_resources = {r["name"]: r for r in self.uma.resource_set_list()}
            existing_roles = {r["name"]: r for r in self.admin.get_client_roles(client_id=client_uuid)}
            existing_policies = {p["name"]: p for p in self.admin.get_client_authz_policies(client_id=client_uuid)}
            existing_permissions = {p["name"]: p for p in self.admin.get_client_authz_permissions(client_id=client_uuid)}
            logger.info("✔ Configurations loaded.")
        except KeycloakGetError as e:
            logger.error(
                f"\n❌ Could not load client configurations. "
                f"Please check if client UUID '{client_uuid}' is correct and has Authorization enabled."
            )
            logger.error(f"   Keycloak error: {e}")
            return

        # 4) Define the three policies and which scopes they grant
        policy_definitions = {
            "admin": ["list", "show", "create", "edit", "delete", "export"],
            "standard": ["list", "show", "create", "edit", "export"],
            "view-only": ["list", "show"],
        }
        policy_ids = {}

        logger.info("---")
        logger.info("Setting up core policies: admin, standard, view-only...")

        for policy_name in policy_definitions.keys():
            role_id = None
            policy_id = None

            # a) Create or get client role for the policy
            try:
                if policy_name in existing_roles:
                    role_id = existing_roles[policy_name]["id"]
                    logger.info(f"  ✔ Client role exists: {policy_name}")
                else:
                    # Try to get existing role first
                    try:
                        role = self.admin.get_client_role(client_id=client_uuid, role_name=policy_name)
                        role_id = role["id"]
                        existing_roles[policy_name] = role
                        logger.info(f"  ✔ Client role found: {policy_name}")
                    except:
                        # Role doesn't exist, create it
                        role_payload = {
                            "name": policy_name,
                            "description": f"Role for {policy_name} policy",
                            "clientRole": True,
                            "composite": False,
                        }
                        self.admin.create_client_role(
                            client_role_id=client_uuid,
                            payload=role_payload,
                            skip_exists=True
                        )
                        role = self.admin.get_client_role(client_id=client_uuid, role_name=policy_name)
                        role_id = role["id"]
                        existing_roles[policy_name] = role
                        logger.info(f"  ✨ Created client role: {policy_name}")

            except Exception as e:
                logger.error(f"❌ Failed to create/get role {policy_name}: {e}")
                continue

            # b) Create or get role-based policy
            # b) Create or get role-based policy
            try:
                full_policy_name = f"Policy - {policy_name}"

                if full_policy_name in existing_policies:
                    policy_id = existing_policies[full_policy_name]["id"]
                    logger.info(f"  ✔ Role policy exists: {full_policy_name}")
                else:
                    # Alternative approach: Try using the generic policy creation method
                    try:
                        # Method 1: Use the specific role-based policy creation
                        roles_config = [{"id": role_id, "required": True}]
                        policy_payload = {
                            "name": full_policy_name,
                            "type": "role",
                            "logic": "POSITIVE",
                            "decisionStrategy": "UNANIMOUS",
                            "config": {
                                "roles": json.dumps(roles_config, separators=(',', ':'))
                            }
                        }

                        created_policy = self.admin.create_client_authz_role_based_policy(
                            client_id=client_uuid,
                            payload=policy_payload
                        )

                    except Exception as role_policy_error:
                        # Method 2: Fallback to generic policy creation
                        logger.info(f"    ⚠ Role-based policy creation failed, trying generic method...")

                        policy_payload = {
                            "name": full_policy_name,
                            "type": "role",
                            "logic": "POSITIVE",
                            "decisionStrategy": "UNANIMOUS",
                            "config": {
                                "roles": f'[{{"id":"{role_id}","required":true}}]'  # String format instead of JSON
                            }
                        }

                        # Debug output
                        logger.info(f"    🔍 Trying with config: {policy_payload['config']}")

                        created_policy = self.admin.create_client_authz_policy(
                            client_id=client_uuid,
                            payload=policy_payload
                        )

                    # Handle different response formats
                    if isinstance(created_policy, dict) and "id" in created_policy:
                        policy_id = created_policy["id"]
                    else:
                        # If response doesn't contain ID, refresh policies and find it
                        logger.info(f"    🔄 Refreshing policies to find created policy...")
                        updated_policies = self.admin.get_client_authz_policies(client_id=client_uuid)
                        for policy in updated_policies:
                            if policy["name"] == full_policy_name:
                                policy_id = policy["id"]
                                break

                    if policy_id:
                        existing_policies[full_policy_name] = {"id": policy_id, "name": full_policy_name}
                        logger.info(f"  ✨ Created role policy: {full_policy_name}")
                    else:
                        raise Exception("Policy created but ID not found")

            except Exception as e:
                logger.error(f"❌ Failed to create policy {full_policy_name}: {e}")
                # Add more detailed error information
                if hasattr(e, 'response') and hasattr(e.response, 'text'):
                    logger.error(f"    Response: {e.response.text}")
                continue
            # Only add to policy_ids if both role and policy were created successfully
            if role_id and policy_id:
                policy_ids[policy_name] = policy_id
                logger.info(f"  ✅ Policy setup complete for: {policy_name} (ID: {policy_id})")
            else:
                logger.error(f"❌ Failed to setup policy: {policy_name}")

        # Debug: Print policy_ids to verify they were created
        logger.info(f"\n🔍 Policy IDs created: {policy_ids}")

        if not policy_ids:
            logger.error(
                "\n❌ No policies were created successfully. Cannot proceed with permissions."
            )
            return

        # 5) Iterate over all Django models to create resources and permissions
        scopes = ["list", "show", "create", "edit", "delete", "export"]

        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            logger.info("---")
            logger.info(f"Processing Model: {res_name}")

            # --- Create or fetch UMA resource-set for the model ---
            if res_name in existing_resources:
                resource_id = existing_resources[res_name].get("_id") or existing_resources[res_name].get("id")
                logger.info(f"  ✔ UMA resource exists: {res_name}")
            else:
                payload = {
                    "name": res_name,
                    "displayName": res_name,
                    "type": "django_model",
                    "scopes": [{"name": s} for s in scopes],
                    "ownerManagedAccess": False,
                }
                try:
                    created = self.uma.resource_set_create(payload)
                    resource_id = created.get("_id") or created.get("id")
                    existing_resources[res_name] = created
                    logger.info(f"  ✨ Created UMA resource: {res_name}")
                except Exception as e:
                    logger.error(f"Failed to create resource {res_name}: {e}")
                    continue

            # --- Create one resource-based permission per policy (not per scope) ---
            for policy_name, scopes_for_policy in policy_definitions.items():
                # Skip if this policy wasn't created successfully
                if policy_name not in policy_ids:
                    logger.info(f"    ⏭ Skipping policy {policy_name} - not available")
                    continue

                perm_name = f"Permission - {res_name} - {policy_name}"

                if perm_name in existing_permissions:
                    logger.info(f"    ✔ Resource permission exists: {perm_name}")
                    continue

                # Get the policy ID for this policy
                policy_id = policy_ids[policy_name]

                # Create resource-based permission that grants the scopes defined for this policy
                permission_payload = {
                    "name": perm_name,
                    "type": "resource",  # Changed from "scope" to "resource"
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "resources": [resource_id],
                    "scopes": scopes_for_policy,  # All scopes this policy grants for this resource
                    "policies": [policy_id],  # Link to the specific policy
                }

                try:
                    self.admin.create_client_authz_resource_based_permission(
                        client_id=client_uuid,
                        payload=permission_payload
                    )
                    existing_permissions[perm_name] = {"name": perm_name}
                    logger.info(
                        f"    🛡 Created resource permission: {perm_name}"
                    )
                    logger.info(f"        └── Grants scopes: {', '.join(scopes_for_policy)}")

                except KeycloakPostError as e:
                    logger.error(
                            f"    ❌ Failed to create permission {perm_name}: {e.response.text if hasattr(e, 'response') else e}"
                    )
                except Exception as e:
                    logger.error(
                        f"    ❌ Failed to create permission {perm_name}: {e}")


        logger.info("\n---")
        logger.info("Keycloak authorization setup complete.")

        # Print summary
        logger.info("\n📊 Summary:")
        total_models = len([m for m in apps.get_models()])
        total_policies = len(policy_definitions)
        max_permissions = total_models * total_policies
        logger.info(f"  • Models processed: {total_models}")
        logger.info(f"  • Policies created: {total_policies}")
        logger.info(f"  • Max permissions possible: {max_permissions}")
        logger.info("\n🔐 Permission Structure:")
        for policy_name, scopes in policy_definitions.items():
            logger.info(f"  • {policy_name}: {', '.join(scopes)}")



    def get_authz_permissions(self):
        permissions = self.admin.get_client_authz_permissions(client_id=self.client_uuid)
        return permissions

    def get_permissions(self, access_token: str, resource_name: str, resource_id: str = None):
        """
        Gets the allowed actions for a user on a specific resource.

        Args:
            access_token (str): The user's access token.
            resource_name (str): The name of the resource (e.g., 'lex_app.MyModel').
            resource_id (str, optional): The specific ID of the record. Defaults to None.

        Returns:
            A set of allowed scopes (e.g., {'edit', 'view'}).
        """
        if not self.oidc:
            logger.error("OIDC client not initialized.")
            if self.retry():
                return set()
        try:
            uma_permissions  = self.uma.resource_set_list()
            allowed_scopes = set()
            for perm in uma_permissions:
                if perm.get('rsname') == resource_name:
                    if resource_id and perm.get('resource_set_id') == resource_id:
                        allowed_scopes.update(perm.get('scopes', []))
                    elif not resource_id:
                        allowed_scopes.update(perm.get('scopes', []))
            return allowed_scopes
        except Exception as e:
            logger.error(f"Failed to get UMA permissions: {e}")
            return set()



    def teardown_django_model_permissions(self):
        pass

