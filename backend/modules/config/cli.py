"""CLI interface for configuration management.

This CLI allows you to:
- Validate configuration files
- List available models
- Inspect configuration values
- Test configuration loading
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict

from .manager import ConfigManager

# Set up logging for CLI
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def validate_config(args) -> None:
    """Validate all configuration files."""
    print("🔍 Validating configuration files...")
    
    config_manager = ConfigManager()
    status = config_manager.validate_config()
    
    print("\n📋 Validation Results:")
    for config_type, is_valid in status.items():
        status_icon = "✅" if is_valid else "❌"
        print(f"  {status_icon} {config_type}: {'Valid' if is_valid else 'Invalid'}")
    
    if all(status.values()):
        print("\n🎉 All configurations are valid!")
        sys.exit(0)
    else:
        print("\n💥 Some configurations have issues. Check logs above.")
        sys.exit(1)


def list_models(args) -> None:
    """List all available LLM models."""
    config_manager = ConfigManager()
    llm_config = config_manager.llm_config
    
    if not llm_config.models:
        print("❌ No models configured")
        return
    
    print(f"📚 Found {len(llm_config.models)} configured models:\n")
    
    for name, model in llm_config.models.items():
        print(f"🤖 {name}")
        print(f"   Model: {model.model_name}")
        print(f"   URL: {model.model_url}")
        print(f"   Max Tokens: {model.max_tokens}")
        print(f"   Temperature: {model.temperature}")
        if model.description:
            print(f"   Description: {model.description}")
        if model.extra_headers:
            print(f"   Extra Headers: {list(model.extra_headers.keys())}")
        print()


def list_servers(args) -> None:
    """List all configured MCP servers."""
    config_manager = ConfigManager()
    mcp_config = config_manager.mcp_config
    
    if not mcp_config.servers:
        print("❌ No MCP servers configured")
        return
    
    print(f"🔧 Found {len(mcp_config.servers)} configured MCP servers:\n")
    
    for name, server in mcp_config.servers.items():
        print(f"🛠️  {name}")
        print(f"   Enabled: {'✅' if server.enabled else '❌'}")
        if server.description:
            print(f"   Description: {server.description}")
        if server.command:
            print(f"   Command: {' '.join(server.command)}")
        if server.url:
            print(f"   URL: {server.url}")
        if server.groups:
            print(f"   Groups: {', '.join(server.groups)}")
        if server.is_exclusive:
            print(f"   Exclusive: ⚠️  Yes")
        print()


def inspect_settings(args) -> None:
    """Inspect application settings."""
    config_manager = ConfigManager()
    settings = config_manager.app_settings
    
    print("⚙️  Application Settings:\n")
    
    # Group settings by category
    categories = {
        "Application": ["app_name", "port", "debug_mode", "log_level"],
        "Features": [attr for attr in dir(settings) if attr.startswith("feature_")],
        "RAG": ["mock_rag", "rag_mock_url"],
        "Banner": ["banner_enabled"],
        "Agent": ["feature_agent_mode_available", "agent_max_steps"],
        "Health": ["llm_health_check_interval", "mcp_health_check_interval"],
        "S3": ["s3_endpoint", "s3_use_mock", "s3_timeout"],
        "Admin": ["admin_group"]
    }
    
    for category, attrs in categories.items():
        print(f"📂 {category}:")
        for attr in attrs:
            if hasattr(settings, attr):
                value = getattr(settings, attr)
                # Hide sensitive values
                if "key" in attr.lower() or "password" in attr.lower():
                    value = "***" if value else "(not set)"
                print(f"   {attr}: {value}")
        print()


def show_config_paths(args) -> None:
    """Show where configuration files are being loaded from."""
    config_manager = ConfigManager()
    
    print("📍 Configuration file search paths:\n")
    
    # Show search paths for each config type
    config_files = {
        "LLM Config": "llmconfig.yml",
        "MCP Config": "mcp.json"
    }
    
    for config_type, filename in config_files.items():
        print(f"🔍 {config_type} ({filename}):")
        search_paths = config_manager._search_paths(filename)
        for path in search_paths:
            exists = "✅" if path.exists() else "❌"
            print(f"   {exists} {path}")
        print()


def reload_config(args) -> None:
    """Reload configuration from files."""
    config_manager = ConfigManager()
    config_manager.reload_configs()
    print("🔄 Configuration reloaded successfully!")


def export_config(args) -> None:
    """Export current configuration as JSON."""
    config_manager = ConfigManager()
    
    config_data = {
        "app_settings": config_manager.app_settings.model_dump(),
        "llm_config": config_manager.llm_config.model_dump(),
        "mcp_config": config_manager.mcp_config.model_dump()
    }
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(config_data, f, indent=2, default=str)
        print(f"📄 Configuration exported to {args.output}")
    else:
        print(json.dumps(config_data, indent=2, default=str))


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Configuration management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m backend.modules.config.cli validate
  python -m backend.modules.config.cli list-models
  python -m backend.modules.config.cli list-servers
  python -m backend.modules.config.cli inspect
  python -m backend.modules.config.cli paths
  python -m backend.modules.config.cli export --output config.json
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate all configuration files')
    validate_parser.set_defaults(func=validate_config)
    
    # List models command
    list_models_parser = subparsers.add_parser('list-models', help='List all configured LLM models')
    list_models_parser.set_defaults(func=list_models)
    
    # List servers command
    list_servers_parser = subparsers.add_parser('list-servers', help='List all configured MCP servers')
    list_servers_parser.set_defaults(func=list_servers)
    
    # Inspect settings command
    inspect_parser = subparsers.add_parser('inspect', help='Inspect application settings')
    inspect_parser.set_defaults(func=inspect_settings)
    
    # Show paths command
    paths_parser = subparsers.add_parser('paths', help='Show configuration file search paths')
    paths_parser.set_defaults(func=show_config_paths)
    
    # Reload command
    reload_parser = subparsers.add_parser('reload', help='Reload configuration from files')
    reload_parser.set_defaults(func=reload_config)
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export current configuration as JSON')
    export_parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    export_parser.set_defaults(func=export_config)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()