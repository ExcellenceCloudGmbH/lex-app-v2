# lex_app/rest_api/views/authentication/token_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from datetime import datetime, timezone, timedelta
import jwt
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class StreamlitAuthTokenView(APIView):
    """Generate JWT token for Streamlit iframe authentication"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Generate and return JWT token for authenticated user"""
        try:
            user = request.user

            # Use UTC timestamps to avoid timezone issues
            now = datetime.now(timezone.utc)
            exp_time = now + timedelta(hours=2)

            # Convert to Unix timestamps (seconds since epoch)
            iat_timestamp = int(now.timestamp())
            exp_timestamp = int(exp_time.timestamp())

            # Get user permissions (your existing logic)
            permissions = []
            try:
                access_token = None
                auth_header = request.META.get('HTTP_AUTHORIZATION', '')
                if auth_header.startswith('Bearer '):
                    access_token = auth_header[7:]

                if access_token:
                    from .KeycloakManager import KeycloakManager
                    kc_manager = KeycloakManager()
                    permissions = kc_manager.get_uma_permissions(access_token)

            except Exception as e:
                logger.warning(f"Failed to get permissions for JWT token: {e}")
                permissions = []

            # Create JWT payload with Unix timestamps
            payload = {
                'user_id': user.id,
                'email': getattr(user, 'email', ''),
                'username': getattr(user, 'username', ''),
                'first_name': getattr(user, 'first_name', ''),
                'last_name': getattr(user, 'last_name', ''),
                'permissions': permissions,
                'exp': exp_timestamp,  # Unix timestamp
                'iat': iat_timestamp,  # Unix timestamp
                'iss': 'lex-backend',
            }

            # Generate JWT token
            token = jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

            logger.info(f"Generated JWT token for user {user.email}")
            logger.debug(f"Token timestamps - IAT: {iat_timestamp}, EXP: {exp_timestamp}")

            return Response({
                'token': token,
                'expires_in': 7200,  # 2 hours in seconds
                'expires_at': exp_time.isoformat(),
                'user': {
                    'id': user.id,
                    'email': payload['email'],
                    'username': payload['username'],
                    'full_name': f"{payload['first_name']} {payload['last_name']}".strip()
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to generate JWT token: {str(e)}")
            return Response({
                'error': 'Failed to generate authentication token',
                'detail': str(e) if settings.DEBUG else 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StreamlitPermissionsView(APIView):
    """
    Get user permissions for JWT-authenticated Streamlit requests.
    This is for when Streamlit needs to fetch permissions separately.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Validate JWT token and return permissions"""
        try:
            # Get JWT token from request body
            token = request.data.get('token')
            if not token:
                return Response({
                    'error': 'No token provided'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Decode and validate JWT token
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            except jwt.ExpiredSignatureError:
                return Response({
                    'error': 'Token expired'
                }, status=status.HTTP_401_UNAUTHORIZED)
            except jwt.InvalidTokenError:
                return Response({
                    'error': 'Invalid token'
                }, status=status.HTTP_401_UNAUTHORIZED)

            # Return permissions (already included in JWT payload)
            return Response({
                'permissions': payload.get('permissions', []),
                'user': {
                    'id': payload.get('user_id'),
                    'email': payload.get('email'),
                    'username': payload.get('username')
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to get Streamlit permissions: {str(e)}")
            return Response({
                'error': 'Failed to get permissions'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
