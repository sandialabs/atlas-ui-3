#!/usr/bin/env python3
"""
Unit tests for organize_files_metadata function to ensure it handles
last_modified field correctly as both datetime objects and ISO strings.
"""

import pytest
from datetime import datetime
from backend.modules.file_storage.manager import FileManager


class TestOrganizeFilesMetadata:
    """Test organize_files_metadata handles different last_modified formats"""
    
    def test_organize_with_datetime_object(self):
        """Test that datetime objects are converted to ISO format"""
        file_manager = FileManager(s3_client=None)
        
        now = datetime.now()
        file_references = {
            "test.txt": {
                "key": "users/test@test.com/test.txt",
                "size": 100,
                "content_type": "text/plain",
                "last_modified": now,
                "tags": {"source": "user"}
            }
        }
        
        result = file_manager.organize_files_metadata(file_references)
        
        assert result["total_files"] == 1
        assert len(result["files"]) == 1
        assert result["files"][0]["filename"] == "test.txt"
        assert result["files"][0]["last_modified"] == now.isoformat()
    
    def test_organize_with_iso_string(self):
        """Test that ISO strings are preserved as-is"""
        file_manager = FileManager(s3_client=None)
        
        iso_string = "2025-11-02T12:00:00"
        file_references = {
            "test.txt": {
                "key": "users/test@test.com/test.txt",
                "size": 100,
                "content_type": "text/plain",
                "last_modified": iso_string,
                "tags": {"source": "user"}
            }
        }
        
        result = file_manager.organize_files_metadata(file_references)
        
        assert result["total_files"] == 1
        assert len(result["files"]) == 1
        assert result["files"][0]["filename"] == "test.txt"
        assert result["files"][0]["last_modified"] == iso_string
    
    def test_organize_with_none_last_modified(self):
        """Test that None last_modified is handled correctly"""
        file_manager = FileManager(s3_client=None)
        
        file_references = {
            "test.txt": {
                "key": "users/test@test.com/test.txt",
                "size": 100,
                "content_type": "text/plain",
                "last_modified": None,
                "tags": {"source": "user"}
            }
        }
        
        result = file_manager.organize_files_metadata(file_references)
        
        assert result["total_files"] == 1
        assert len(result["files"]) == 1
        assert result["files"][0]["filename"] == "test.txt"
        assert result["files"][0]["last_modified"] is None
    
    def test_organize_without_last_modified_key(self):
        """Test that missing last_modified key is handled correctly"""
        file_manager = FileManager(s3_client=None)
        
        file_references = {
            "test.txt": {
                "key": "users/test@test.com/test.txt",
                "size": 100,
                "content_type": "text/plain",
                "tags": {"source": "user"}
            }
        }
        
        result = file_manager.organize_files_metadata(file_references)
        
        assert result["total_files"] == 1
        assert len(result["files"]) == 1
        assert result["files"][0]["filename"] == "test.txt"
        assert result["files"][0]["last_modified"] is None
    
    def test_organize_multiple_files_mixed_formats(self):
        """Test handling multiple files with different last_modified formats"""
        file_manager = FileManager(s3_client=None)
        
        now = datetime.now()
        iso_string = "2025-11-02T12:00:00"
        
        file_references = {
            "file1.txt": {
                "key": "users/test@test.com/file1.txt",
                "size": 100,
                "content_type": "text/plain",
                "last_modified": now,
                "tags": {"source": "user"}
            },
            "file2.txt": {
                "key": "users/test@test.com/file2.txt",
                "size": 200,
                "content_type": "text/plain",
                "last_modified": iso_string,
                "tags": {"source": "tool"}
            },
            "file3.txt": {
                "key": "users/test@test.com/file3.txt",
                "size": 300,
                "content_type": "text/plain",
                "last_modified": None,
                "tags": {"source": "user"}
            }
        }
        
        result = file_manager.organize_files_metadata(file_references)
        
        assert result["total_files"] == 3
        assert len(result["files"]) == 3
        
        # Find each file by name and check last_modified
        files_by_name = {f["filename"]: f for f in result["files"]}
        assert files_by_name["file1.txt"]["last_modified"] == now.isoformat()
        assert files_by_name["file2.txt"]["last_modified"] == iso_string
        assert files_by_name["file3.txt"]["last_modified"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
