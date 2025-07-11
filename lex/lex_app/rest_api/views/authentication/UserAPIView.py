from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated


class UserAPIView(APIView):
    """
    GET /api/user/  → 200 + { id, username, full_name, email, roles }
    any other method → 405
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        user = request.user

        # collect Django-group names
        django_roles = [g.name for g in user.groups.all()]

        # if you’re using mozilla-django-oidc and want to expose token roles:
        oidc_roles = []
        token = getattr(request, "auth", None)
        if isinstance(token, dict):
            oidc_roles = token.get("realm_access", {}).get("roles", [])

        return Response(
            {
                "id": user.id,
                "username": user.get_username(),
                "full_name": user.get_full_name(),
                "email": user.email,
                "roles": list(set(django_roles + oidc_roles)),
            },
            status=status.HTTP_200_OK,
        )
