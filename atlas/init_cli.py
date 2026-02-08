"""
Atlas Init CLI - Set up configuration files for Atlas.

Usage:
    atlas-init                    # Interactive setup in current directory
    atlas-init --target ./myapp   # Setup in specific directory
    atlas-init --minimal          # Create minimal .env only
    atlas-init --force            # Overwrite existing files without prompting
"""

import argparse
import shutil
import sys
from pathlib import Path


def get_package_root() -> Path:
    """Get the root directory of the atlas package."""
    return Path(__file__).resolve().parent


def get_config_dir() -> Path:
    """Get the path to config/ in the atlas package (package defaults)."""
    return get_package_root() / "config"


def get_env_example_path() -> Path:
    """Get the path to .env.example in the package."""
    return get_package_root() / ".env.example"


def prompt_yes_no(message: str, default: bool = False) -> bool:
    """Prompt user for yes/no confirmation."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        response = input(message + suffix).strip().lower()
        if response == "":
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'")


def copy_with_prompt(src: Path, dst: Path, force: bool = False) -> bool:
    """Copy a file, prompting if destination exists."""
    if dst.exists() and not force:
        if not prompt_yes_no(f"  {dst} already exists. Overwrite?"):
            print(f"  Skipping {dst.name}")
            return False

    if src.is_dir():
        if dst.is_symlink():
            dst.unlink()
        elif dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

    print(f"  Created {dst}")
    return True


def create_minimal_env(target_dir: Path, force: bool = False) -> bool:
    """Create a minimal .env file with just API key placeholders."""
    env_path = target_dir / ".env"

    if env_path.exists() and not force:
        if not prompt_yes_no(f"  {env_path} already exists. Overwrite?"):
            print(f"  Skipping {env_path.name}")
            return False

    minimal_env = """\
# Atlas Configuration
# See https://github.com/sandialabs/atlas-ui-3 for full documentation

# =============================================================================
# LLM API Keys (set at least one)
# =============================================================================
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
# GOOGLE_API_KEY=your-google-api-key-here

# =============================================================================
# Server Settings
# =============================================================================
PORT=8000
DEBUG_MODE=true

# =============================================================================
# Optional: Custom config location
# Uncomment and set if you have custom config files
# =============================================================================
# APP_CONFIG_DIR=./config

# =============================================================================
# Optional: RAG Configuration
# =============================================================================
# FEATURE_RAG_ENABLED=false
# ATLAS_RAG_URL=https://your-rag-api.example.com
# ATLAS_RAG_BEARER_TOKEN=your-api-key-here
"""

    env_path.write_text(minimal_env, encoding="utf-8")
    print(f"  Created {env_path}")
    return True


def run_init(args: argparse.Namespace) -> int:
    """Run the atlas-init command."""
    target_dir = Path(args.target).resolve()

    # Ensure target directory exists
    if not target_dir.exists():
        if not args.force:
            if not prompt_yes_no(f"Directory {target_dir} does not exist. Create it?", default=True):
                print("Aborted.")
                return 1
        target_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {target_dir}")

    print(f"\nSetting up Atlas configuration in: {target_dir}\n")

    package_config = get_config_dir()
    env_example = get_env_example_path()

    if args.minimal:
        # Minimal mode: just create a simple .env
        print("Creating minimal configuration...")
        create_minimal_env(target_dir, force=args.force)
    else:
        # Full mode: copy config and .env
        print("Copying configuration files...")

        # Copy atlas/config/ to target/config (excluding mcp-example-configs)
        if package_config.exists():
            target_config = target_dir / "config"
            # Copy individual config files (not subdirectories like mcp-example-configs)
            target_config.mkdir(parents=True, exist_ok=True)
            for src_file in package_config.iterdir():
                if src_file.is_file():
                    copy_with_prompt(src_file, target_config / src_file.name, force=args.force)
        else:
            print(f"  Warning: Package config not found at {package_config}")

        # Copy .env.example to .env
        if env_example.exists():
            target_env = target_dir / ".env"
            if copy_with_prompt(env_example, target_env, force=args.force):
                print("\n  Remember to edit .env and add your API keys!")
        else:
            # Fall back to creating minimal env
            print("  .env.example not found, creating minimal .env...")
            create_minimal_env(target_dir, force=args.force)

    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print("\nNext steps:")
    print(f"  1. Edit {target_dir / '.env'} and add your API keys")
    print("  2. Run: atlas-chat 'Hello, world!'")
    print("  3. Or start the server: atlas-server")

    if not args.minimal:
        print(f"\nConfig files are in: {target_dir / 'config'}")
        print("  - llmconfig.yml: LLM model configurations")
        print("  - mcp.json: MCP tool server configurations")

    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for atlas-init CLI."""
    parser = argparse.ArgumentParser(
        prog="atlas-init",
        description="Set up Atlas configuration files in your project directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  atlas-init                    Set up config in current directory
  atlas-init --target ./myapp   Set up config in ./myapp
  atlas-init --minimal          Create only a minimal .env file
  atlas-init --force            Overwrite existing files without prompting

After running atlas-init, edit the .env file to add your API keys.
""",
    )
    parser.add_argument(
        "--target",
        "-t",
        default=".",
        help="Target directory for configuration files (default: current directory).",
    )
    parser.add_argument(
        "--minimal",
        "-m",
        action="store_true",
        help="Create only a minimal .env file (no config folder).",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing files without prompting.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit.",
    )
    return parser


def main() -> None:
    """Main entry point for atlas-init CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        from atlas.version import VERSION

        print(f"atlas-init version {VERSION}")
        sys.exit(0)

    sys.exit(run_init(args))


if __name__ == "__main__":
    main()
