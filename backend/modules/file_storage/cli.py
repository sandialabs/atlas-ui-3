"""CLI interface for file storage operations.

This CLI allows you to:
- Upload files to S3
- List files for users
- Download files from S3
- Get file statistics
- Test file storage operations
"""

import argparse
import base64
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .s3_client import S3StorageClient
from .manager import FileManager

# Set up logging for CLI
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


async def upload_file(args) -> None:
    """Upload a file to S3 storage."""
    file_path = Path(args.file_path)
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    if not args.user_email:
        print("❌ User email is required")
        return
    
    print(f"📤 Uploading {file_path.name} for user {args.user_email}...")
    
    try:
        # Read and encode file content
        with open(file_path, 'rb') as f:
            content = f.read()
        content_base64 = base64.b64encode(content).decode('utf-8')
        
        # Use specified filename or original filename
        filename = args.filename or file_path.name
        
        # Initialize file manager and upload
        file_manager = FileManager()
        result = await file_manager.upload_file(
            user_email=args.user_email,
            filename=filename,
            content_base64=content_base64,
            source_type=args.source_type
        )
        
        print(f"✅ File uploaded successfully!")
        print(f"   S3 Key: {result['key']}")
        print(f"   Size: {result.get('size', 'unknown')} bytes")
        print(f"   Content Type: {result.get('content_type', 'unknown')}")
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        logger.error(f"Upload error: {e}")


async def list_files(args) -> None:
    """List files for a user."""
    if not args.user_email:
        print("❌ User email is required")
        return
    
    print(f"📂 Listing files for user {args.user_email}...")
    
    try:
        s3_client = S3StorageClient()
        files = await s3_client.list_files(
            user_email=args.user_email,
            file_type=args.file_type,
            limit=args.limit
        )
        
        if not files:
            print("📭 No files found")
            return
        
        print(f"\n📋 Found {len(files)} file(s):\n")
        
        # Group files by type if no specific filter
        if not args.file_type:
            user_files = [f for f in files if f.get('tags', {}).get('source') == 'user']
            tool_files = [f for f in files if f.get('tags', {}).get('source') == 'tool']
            
            if user_files:
                print("👤 User Files:")
                for file_info in user_files:
                    print(f"   📄 {file_info['filename']}")
                    print(f"      Key: {file_info['key']}")
                    print(f"      Size: {file_info.get('size', 0)} bytes")
                    print(f"      Type: {file_info.get('content_type', 'unknown')}")
                    print(f"      Modified: {file_info.get('last_modified', 'unknown')}")
                    print()
            
            if tool_files:
                print("🔧 Tool-Generated Files:")
                for file_info in tool_files:
                    tags = file_info.get('tags', {})
                    print(f"   📄 {file_info['filename']}")
                    print(f"      Key: {file_info['key']}")
                    print(f"      Size: {file_info.get('size', 0)} bytes")
                    print(f"      Source Tool: {tags.get('source_tool', 'unknown')}")
                    print(f"      Modified: {file_info.get('last_modified', 'unknown')}")
                    print()
        else:
            for file_info in files:
                print(f"📄 {file_info['filename']}")
                print(f"   Key: {file_info['key']}")
                print(f"   Size: {file_info.get('size', 0)} bytes")
                print(f"   Type: {file_info.get('content_type', 'unknown')}")
                print(f"   Modified: {file_info.get('last_modified', 'unknown')}")
                print()
        
    except Exception as e:
        print(f"❌ List failed: {e}")
        logger.error(f"List error: {e}")


async def download_file(args) -> None:
    """Download a file from S3 storage."""
    if not args.user_email:
        print("❌ User email is required")
        return
    
    if not args.s3_key:
        print("❌ S3 key is required")
        return
    
    print(f"📥 Downloading file {args.s3_key} for user {args.user_email}...")
    
    try:
        s3_client = S3StorageClient()
        file_data = await s3_client.get_file(args.user_email, args.s3_key)
        
        if not file_data:
            print("❌ File not found")
            return
        
        # Decode base64 content
        content = base64.b64decode(file_data['content_base64'])
        
        # Determine output filename
        output_path = Path(args.output) if args.output else Path(file_data['filename'])
        
        # Write to file
        with open(output_path, 'wb') as f:
            f.write(content)
        
        print(f"✅ File downloaded successfully!")
        print(f"   Saved to: {output_path}")
        print(f"   Size: {len(content)} bytes")
        
    except Exception as e:
        print(f"❌ Download failed: {e}")
        logger.error(f"Download error: {e}")


async def delete_file(args) -> None:
    """Delete a file from S3 storage."""
    if not args.user_email:
        print("❌ User email is required")
        return
    
    if not args.s3_key:
        print("❌ S3 key is required")
        return
    
    print(f"🗑️  Deleting file {args.s3_key} for user {args.user_email}...")
    
    if not args.force:
        confirm = input("⚠️  Are you sure? This action cannot be undone. (y/N): ")
        if confirm.lower() != 'y':
            print("❌ Deletion cancelled")
            return
    
    try:
        s3_client = S3StorageClient()
        success = await s3_client.delete_file(args.user_email, args.s3_key)
        
        if success:
            print("✅ File deleted successfully!")
        else:
            print("❌ File not found or already deleted")
        
    except Exception as e:
        print(f"❌ Deletion failed: {e}")
        logger.error(f"Deletion error: {e}")


async def get_stats(args) -> None:
    """Get file statistics for a user."""
    if not args.user_email:
        print("❌ User email is required")
        return
    
    print(f"📊 Getting file statistics for user {args.user_email}...")
    
    try:
        s3_client = S3StorageClient()
        stats = await s3_client.get_user_stats(args.user_email)
        
        print(f"\n📈 File Statistics:\n")
        print(f"   📁 Total Files: {stats.get('total_files', 0)}")
        print(f"   💾 Total Size: {stats.get('total_size_bytes', 0)} bytes")
        print(f"   📤 User Files: {stats.get('user_files', 0)}")
        print(f"   🔧 Tool Files: {stats.get('tool_files', 0)}")
        
        if 'file_types' in stats:
            print(f"\n📊 By File Type:")
            for file_type, count in stats['file_types'].items():
                print(f"   {file_type}: {count}")
        
    except Exception as e:
        print(f"❌ Stats failed: {e}")
        logger.error(f"Stats error: {e}")


def test_categorization(args) -> None:
    """Test file categorization and content type detection."""
    if not args.filename:
        print("❌ Filename is required")
        return
    
    file_manager = FileManager()
    
    print(f"🧪 Testing file categorization for: {args.filename}\n")
    
    content_type = file_manager.get_content_type(args.filename)
    category = file_manager.categorize_file_type(args.filename)
    extension = file_manager.get_file_extension(args.filename)
    canvas_type = file_manager.get_canvas_file_type(extension.lower())
    should_display = file_manager.should_display_in_canvas(args.filename)
    
    print(f"📄 Content Type: {content_type}")
    print(f"🏷️  Category: {category}")
    print(f"📎 Extension: {extension}")
    print(f"🎨 Canvas Type: {canvas_type}")
    print(f"👁️  Display in Canvas: {'✅ Yes' if should_display else '❌ No'}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="File storage management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m backend.modules.file_storage.cli upload test.txt user@example.com
  python -m backend.modules.file_storage.cli list user@example.com
  python -m backend.modules.file_storage.cli download user@example.com file_key_123 --output downloaded.txt
  python -m backend.modules.file_storage.cli stats user@example.com
  python -m backend.modules.file_storage.cli test-categorization example.py
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Upload command
    upload_parser = subparsers.add_parser('upload', help='Upload a file to S3')
    upload_parser.add_argument('file_path', help='Path to file to upload')
    upload_parser.add_argument('user_email', help='User email')
    upload_parser.add_argument('--filename', help='Custom filename (default: use original)')
    upload_parser.add_argument('--source-type', default='user', choices=['user', 'tool'], help='Source type')
    upload_parser.set_defaults(func=upload_file)
    
    # List command
    list_parser = subparsers.add_parser('list', help='List files for a user')
    list_parser.add_argument('user_email', help='User email')
    list_parser.add_argument('--file-type', choices=['user', 'tool'], help='Filter by file type')
    list_parser.add_argument('--limit', type=int, default=100, help='Maximum files to return')
    list_parser.set_defaults(func=list_files)
    
    # Download command
    download_parser = subparsers.add_parser('download', help='Download a file from S3')
    download_parser.add_argument('user_email', help='User email')
    download_parser.add_argument('s3_key', help='S3 key of file to download')
    download_parser.add_argument('--output', '-o', help='Output filename (default: original filename)')
    download_parser.set_defaults(func=download_file)
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a file from S3')
    delete_parser.add_argument('user_email', help='User email')
    delete_parser.add_argument('s3_key', help='S3 key of file to delete')
    delete_parser.add_argument('--force', '-f', action='store_true', help='Skip confirmation')
    delete_parser.set_defaults(func=delete_file)
    
    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Get file statistics for a user')
    stats_parser.add_argument('user_email', help='User email')
    stats_parser.set_defaults(func=get_stats)
    
    # Test categorization command
    test_parser = subparsers.add_parser('test-categorization', help='Test file categorization')
    test_parser.add_argument('filename', help='Filename to test')
    test_parser.set_defaults(func=test_categorization)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if hasattr(args, 'func'):
            if args.command in ['upload', 'list', 'download', 'delete', 'stats']:
                # Async commands
                import asyncio
                asyncio.run(args.func(args))
            else:
                # Sync commands
                args.func(args)
    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()