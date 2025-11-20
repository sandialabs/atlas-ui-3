"""Authentication and authorization module."""

import logging
from typing import Optional

import httpx
from modules.config.config_manager import config_manager

logger = logging.getLogger(__name__)


async def is_user_in_group(user_id: str, group_id: str) -> bool:
    """
    Check if a user is in a specified group.

    This function first checks for a configured external authorization endpoint.
    If available, it makes an HTTP request to check group membership.
    If not configured, it falls back to a mock implementation for local development.

    Args:
        user_id: User email/identifier.
        group_id: Group identifier.

    Returns:
        True if the user is in the group, False otherwise.
    """
    app_settings = config_manager.app_settings
    auth_url = app_settings.auth_group_check_url
    api_key = app_settings.auth_group_check_api_key

    if auth_url and api_key:
        # Use the external HTTP endpoint for authorization
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                payload = {"user_id": user_id, "group_id": group_id}
                response = await client.post(auth_url, json=payload, headers=headers, timeout=5.0)
                response.raise_for_status()
                # Assuming the endpoint returns a simple JSON like {"is_member": true}
                return response.json().get("is_member", False)
        except httpx.RequestError as e:
            logger.error(f"HTTP request to auth endpoint failed: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Error during external auth check: {e}", exc_info=True)
            return False
    else:
        # Fallback to mock implementation if no external endpoint is configured
        if (app_settings.debug_mode and
                user_id == app_settings.test_user and
                group_id == app_settings.admin_group):
            return True

        mock_groups = {
            "test@test.com": ["users", "mcp_basic", "admin"],
            "user@example.com": ["users", "mcp_basic"],
            "admin@example.com": ["admin", "users", "mcp_basic", "mcp_advanced"]
        }
        user_groups = mock_groups.get(user_id, [])
        return group_id in user_groups


def get_user_from_aws_alb_jwt(encoded_jwt, expected_alb_arn, aws_region):
    """
    Validates the AWS ALB JWT and parses the email address from the payload.

    Args:
        encoded_jwt (str): The JWT from the x-amzn-oidc-data header.
        expected_alb_arn (str): The ARN of your Application Load Balancer.
        aws_region (str): The AWS region where your ALB is located (e.g., 'us-east-1').

    Returns:
        str: The user's email address, or None if validation fails.
    """
    try:
        # Step 1: Decode the JWT header to get the key ID (kid) and signer
        jwt_headers_encoded = encoded_jwt.split('.')[0]
        # JWTs use base64url encoding, not standard base64
        # Add padding if missing, as Python's b64decode expects it
        jwt_headers_decoded = base64.b64decode(jwt_headers_encoded + '===').decode("utf-8")
        decoded_json_headers = json.loads(jwt_headers_decoded)
        kid = decoded_json_headers['kid']
        received_alb_arn = decoded_json_headers.get('signer')

        # Step 2: Validate the signer matches the expected ALB ARN
        if received_alb_arn != expected_alb_arn:
            print(f"Error: Invalid signer ARN. Expected {expected_alb_arn}, got {received_alb_arn}")
            return None

        # Step 3: Get the public key from the regional endpoint
        url = f'https://public-keys.auth.elb.{aws_region}.amazonaws.com/{kid}'
        req = requests.get(url)
        pub_key = req.text

        # Step 4: Validate the signature and claims using PyJWT
        # The decode method handles signature verification and standard claims (like expiration)
        # The ALB uses ES256 algorithm
        payload = jwt.decode(
            encoded_jwt,
            pub_key,
            algorithms=['ES256'],
            # Optional: Add audience or issuer validation if needed, though ALB handles most standard claims validation
            options={"verify_aud": False, "verify_iss": False}
        )

        # Step 5: Extract the email address from the payload
        email_address = payload.get('email')
        if email_address:
            return email_address
        else:
            print("Error: 'email' claim not found in JWT payload.")
            return None

    except jwt.ExpiredSignatureError:
        print("Error: Token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Error: Invalid token - {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching public key: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None


def get_user_from_header(x_email_header: Optional[str]) -> Optional[str]:
    """Extract user email from authentication header value."""
    if not x_email_header:
        return None
    return x_email_header.strip()
