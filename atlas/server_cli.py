"""
Atlas Server CLI - Start the Atlas backend server.

Usage:
    atlas-server                              # Start with defaults
    atlas-server --port 8000                  # Custom port
    atlas-server --env /path/to/.env          # Custom env file
    atlas-server --config-folder /path/to/config  # Custom config folder
"""

import argparse
import os
import sys
from pathlib import Path


def _apply_env_file(env_path: Path) -> None:
    """Load environment variables from a .env file."""
    from dotenv import load_dotenv

    if not env_path.exists():
        print(f"Error: env file not found: {env_path}", file=sys.stderr)
        sys.exit(2)

    load_dotenv(dotenv_path=str(env_path))


def _apply_config_folder(config_folder: Path) -> None:
    """Set up config folder as the overrides directory."""
    if not config_folder.exists():
        print(f"Error: config folder not found: {config_folder}", file=sys.stderr)
        sys.exit(2)

    os.environ["APP_CONFIG_OVERRIDES"] = str(config_folder.resolve())


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for atlas-server CLI."""
    parser = argparse.ArgumentParser(
        prog="atlas-server",
        description="Start the Atlas backend server with MCP integration.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run the server on (default: 8000 or PORT env var).",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind to (default: 127.0.0.1 or ATLAS_HOST env var).",
    )
    parser.add_argument(
        "--env",
        dest="env_file",
        default=None,
        help="Path to .env file (default: .env in current directory or package root).",
    )
    parser.add_argument(
        "--config-folder",
        dest="config_folder",
        default=None,
        help="Path to config folder for overrides (sets APP_CONFIG_OVERRIDES).",
    )
    parser.add_argument(
        "--config-defaults",
        dest="config_defaults",
        default=None,
        help="Path to config defaults folder (sets APP_CONFIG_DEFAULTS).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development (not recommended for production).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit.",
    )
    return parser


def run_server(args: argparse.Namespace) -> int:
    """Run the Atlas server with the given arguments."""
    import uvicorn

    # Determine host and port
    host = args.host or os.getenv("ATLAS_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("PORT", "8000"))

    # Import the FastAPI app
    from atlas.main import app

    print(f"Starting Atlas server on {host}:{port}")

    if args.reload:
        print("Warning: --reload is enabled. This is not recommended for production.")
        uvicorn.run(
            "atlas.main:app",
            host=host,
            port=port,
            reload=True,
            workers=1,  # reload doesn't support multiple workers
        )
    else:
        uvicorn.run(
            app,
            host=host,
            port=port,
            workers=args.workers,
        )

    return 0


def main() -> None:
    """Main entry point for atlas-server CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        from atlas.version import VERSION
        print(f"atlas-server version {VERSION}")
        sys.exit(0)

    # Apply env file first (before any other imports that might use env vars)
    if args.env_file:
        _apply_env_file(Path(args.env_file).expanduser())
    else:
        # Try to find .env in current directory or package root
        cwd_env = Path.cwd() / ".env"
        if cwd_env.exists():
            _apply_env_file(cwd_env)
        else:
            # Check package root (parent of atlas/)
            pkg_root_env = Path(__file__).resolve().parents[1] / ".env"
            if pkg_root_env.exists():
                _apply_env_file(pkg_root_env)

    # Apply config folder if specified
    if args.config_folder:
        _apply_config_folder(Path(args.config_folder).expanduser())

    # Apply config defaults if specified
    if args.config_defaults:
        defaults_path = Path(args.config_defaults).expanduser()
        if not defaults_path.exists():
            print(f"Error: config defaults folder not found: {defaults_path}", file=sys.stderr)
            sys.exit(2)
        os.environ["APP_CONFIG_DEFAULTS"] = str(defaults_path.resolve())

    sys.exit(run_server(args))


if __name__ == "__main__":
    main()
