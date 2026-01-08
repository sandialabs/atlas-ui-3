"""Test to ensure docker-compose.yml environment variables stay in sync with .env.example.

This test ensures that the environment variables set in docker-compose.yml for the atlas-ui
service match those defined in .env.example, with appropriate exceptions for Docker-specific
configurations.
"""

import re
from pathlib import Path


def parse_env_example(env_file_path: Path) -> dict[str, str]:
    """Parse .env.example and extract all environment variables.
    
    Args:
        env_file_path: Path to the .env.example file
        
    Returns:
        Dictionary of environment variable names to values
    """
    env_vars = {}
    with open(env_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
                if match:
                    key, value = match.groups()
                    env_vars[key] = value
    return env_vars


def parse_docker_compose_env(docker_compose_path: Path) -> dict[str, str]:
    """Parse docker-compose.yml and extract environment variables for atlas-ui service.
    
    Args:
        docker_compose_path: Path to the docker-compose.yml file
        
    Returns:
        Dictionary of environment variable names to values
    """
    docker_env_vars = {}
    with open(docker_compose_path, 'r', encoding='utf-8') as f:
        in_atlas_ui_service = False
        in_environment_section = False
        
        for line in f:
            # Detect when we enter the atlas-ui service block
            if 'atlas-ui:' in line:
                in_atlas_ui_service = True
                in_environment_section = False
                continue
            
            # Detect when we enter another service block (exits atlas-ui)
            stripped = line.strip()
            if in_atlas_ui_service and line and stripped and not line[0].isspace() and ':' in line:
                # We've reached a new top-level section
                in_atlas_ui_service = False
                in_environment_section = False
                continue
            
            # Check if we're entering the environment section within atlas-ui
            if in_atlas_ui_service and 'environment:' in line:
                in_environment_section = True
                continue
            
            # Check if we've exited the environment section (e.g., volumes:, depends_on:)
            if in_environment_section and stripped and not stripped.startswith('-') and not stripped.startswith('#'):
                if ':' in line and not stripped.startswith('- '):
                    # We've reached a new subsection (like volumes:)
                    in_environment_section = False
                    continue
            
            # Parse environment variables
            if in_environment_section and stripped.startswith('- '):
                # Extract env var, handling quoted values
                env_line = stripped[2:]  # Remove "- "
                
                # Handle quoted environment variables
                if env_line.startswith('"'):
                    # Find the closing quote
                    end_quote = env_line.find('"', 1)
                    if end_quote != -1:
                        env_line = env_line[1:end_quote]
                
                match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', env_line)
                if match:
                    key = match.group(1)
                    value = match.group(2).split('#')[0].strip()  # Remove inline comments
                    docker_env_vars[key] = value
                    
    return docker_env_vars


def test_docker_compose_has_required_env_vars():
    """Test that docker-compose.yml includes all required environment variables from .env.example.
    
    This test ensures that Docker deployments have access to the same configuration
    options as local development environments.
    """
    # Get paths to the files
    repo_root = Path(__file__).parent.parent.parent
    env_example_path = repo_root / '.env.example'
    docker_compose_path = repo_root / 'docker-compose.yml'
    
    # Parse both files
    env_example_vars = parse_env_example(env_example_path)
    docker_compose_vars = parse_docker_compose_env(docker_compose_path)
    
    # Define variables that are intentionally different between .env.example and docker-compose.yml
    # These have valid Docker-specific reasons to be different or omitted
    docker_specific_exceptions = {
        'USE_MOCK_S3',  # Docker uses real MinIO, not mock S3
        'VITE_APP_NAME',  # Build-time arg, not runtime env var in docker-compose
        'VITE_FEATURE_POWERED_BY_ATLAS',  # Build-time arg, not runtime env var in docker-compose
        # Note: The following are in .env.example as commented out, not as active vars,
        # so they won't appear in env_example_vars and don't need to be listed here:
        # - ATLAS_HOST (Docker-specific, set to 0.0.0.0 for container networking)
        # - Various MCP/proxy secret headers that are commented out in .env.example
    }
    
    # Find variables in .env.example but not in docker-compose.yml
    missing_vars = set(env_example_vars.keys()) - set(docker_compose_vars.keys()) - docker_specific_exceptions
    
    # Assert that no required variables are missing
    if missing_vars:
        missing_list = sorted(missing_vars)
        error_msg = (
            f"docker-compose.yml is missing {len(missing_vars)} environment variable(s) "
            f"that are defined in .env.example:\n"
            f"{', '.join(missing_list)}\n\n"
            f"Please add these to the 'environment:' section of the 'atlas-ui' service "
            f"in docker-compose.yml."
        )
        raise AssertionError(error_msg)
    
    # Verify that key feature flags are present (as mentioned in the issue)
    key_feature_flags = [
        'FEATURE_MARKETPLACE_ENABLED',
        'FEATURE_TOOLS_ENABLED',
        'FEATURE_FILES_PANEL_ENABLED',
        'FEATURE_RAG_ENABLED',
    ]
    
    for flag in key_feature_flags:
        assert flag in docker_compose_vars, (
            f"Key feature flag '{flag}' is missing from docker-compose.yml"
        )


def test_docker_compose_env_var_values_reasonable():
    """Test that environment variable values in docker-compose.yml are reasonable.
    
    This is a sanity check to ensure values aren't accidentally corrupted.
    """
    repo_root = Path(__file__).parent.parent.parent
    docker_compose_path = repo_root / 'docker-compose.yml'
    
    docker_compose_vars = parse_docker_compose_env(docker_compose_path)
    
    # Check that boolean feature flags have boolean-like values
    feature_flags = [k for k in docker_compose_vars.keys() if k.startswith('FEATURE_')]
    for flag in feature_flags:
        value = docker_compose_vars[flag].lower()
        assert value in ['true', 'false'], (
            f"Feature flag '{flag}' has non-boolean value: '{docker_compose_vars[flag]}'"
        )
    
    # Check that numeric values are numeric
    numeric_vars = ['PORT', 'AGENT_MAX_STEPS']
    for var in numeric_vars:
        if var in docker_compose_vars:
            value = docker_compose_vars[var]
            assert value.isdigit(), (
                f"Numeric variable '{var}' has non-numeric value: '{value}'"
            )


def test_docker_specific_vars_present():
    """Test that Docker-specific environment variables are correctly set.
    
    These variables have Docker-specific values that differ from .env.example.
    """
    repo_root = Path(__file__).parent.parent.parent
    docker_compose_path = repo_root / 'docker-compose.yml'
    
    docker_compose_vars = parse_docker_compose_env(docker_compose_path)
    
    # ATLAS_HOST should be 0.0.0.0 for Docker (allows external connections)
    assert 'ATLAS_HOST' in docker_compose_vars, (
        "ATLAS_HOST is required in docker-compose.yml for container networking"
    )
    assert docker_compose_vars['ATLAS_HOST'] == '0.0.0.0', (
        "ATLAS_HOST should be 0.0.0.0 in Docker for external access"
    )
    
    # MinIO/S3 configuration should be present for Docker
    s3_vars = ['S3_ENDPOINT', 'S3_BUCKET_NAME', 'S3_ACCESS_KEY', 'S3_SECRET_KEY']
    for var in s3_vars:
        assert var in docker_compose_vars, (
            f"S3 configuration variable '{var}' is required in docker-compose.yml for MinIO integration"
        )
