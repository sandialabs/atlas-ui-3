#!/usr/bin/env python3
"""
Smoke test for S3 Mock Server using FastAPI TestClient.
Tests all supported S3 operations to ensure compatibility.
"""

from fastapi.testclient import TestClient
from main import get_app

# Test configuration
BUCKET = "test-bucket"

def create_test_client():
    """Create FastAPI TestClient for direct testing."""
    app = get_app()
    return TestClient(app)

def test_put_get_object():
    """Test PUT and GET object operations."""
    print("Testing PUT/GET object...")

    client = create_test_client()
    key = "test-file.txt"
    test_data = b"Hello, S3 Mock!"
    tags = "environment=test&type=smoke"

    # PUT object
    headers = {
        "Content-Type": "text/plain",
        "x-amz-meta-author": "test",
        "x-amz-meta-version": "1.0"
    }
    response = client.put(
        f"/{BUCKET}/{key}",
        content=test_data,
        headers=headers,
        params={"tagging": tags}
    )
    assert response.status_code == 200
    etag = response.headers.get("ETag")
    print(f"  PUT successful, ETag: {etag}")

    # GET object
    response = client.get(f"/{BUCKET}/{key}")
    assert response.status_code == 200
    assert response.content == test_data, "Data mismatch"
    assert response.headers["Content-Type"] == "text/plain", "Content-Type mismatch"
    assert response.headers.get("ETag") == etag, "ETag mismatch"
    assert response.headers.get("x-amz-meta-author") == "test", "Metadata mismatch"
    assert response.headers.get("x-amz-meta-version") == "1.0", "Metadata mismatch"

    print("  GET successful, data/metadata verified")

def test_head_object():
    """Test HEAD object operation."""
    print("Testing HEAD object...")

    client = create_test_client()
    key = "test-file.txt"

    response = client.head(f"/{BUCKET}/{key}")
    assert response.status_code == 200

    assert response.headers["Content-Type"] == "text/plain", "Content-Type mismatch"
    assert 'ETag' in response.headers, "ETag missing"
    assert response.headers.get("x-amz-meta-author") == "test", "Metadata mismatch"

    print("  HEAD successful, headers verified")

def test_list_objects():
    """Test ListObjectsV2 operation."""
    print("Testing ListObjectsV2...")

    client = create_test_client()

    # Create test objects for this test
    test_objects = ["list-test-file.txt", "folder/file0.txt", "folder/file1.txt", "folder/file2.txt"]

    for obj_key in test_objects:
        response = client.put(
            f"/{BUCKET}/{obj_key}",
            content=f"Content for {obj_key}".encode(),
            headers={"Content-Type": "text/plain"}
        )
        assert response.status_code == 200

    # List all objects
    response = client.get(f"/{BUCKET}", params={"list-type": "2"})
    assert response.status_code == 200

    # Parse XML response
    import xml.etree.ElementTree as ET
    root = ET.fromstring(response.text)

    # Handle XML namespace
    ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
    contents = root.findall(".//s3:Contents", ns) or root.findall(".//Contents")

    assert len(contents) >= 4, f"Not all objects listed, found {len(contents)}"

    # Check that our test files are present
    keys = []
    for content in contents:
        key_elem = content.find("s3:Key", ns)
        if key_elem is None:
            key_elem = content.find("Key")
        if key_elem is not None and key_elem.text:
            keys.append(key_elem.text)

    print(f"  Expected keys: {test_objects}")
    print(f"  Found keys: {keys}")

    for obj_key in test_objects:
        assert obj_key in keys, f"Test file {obj_key} not in listing"

    print(f"  Listed {len(contents)} objects")

    # Test prefix filtering
    response = client.get(f"/{BUCKET}", params={"list-type": "2", "prefix": "folder/"})
    assert response.status_code == 200

    root = ET.fromstring(response.text)
    contents = root.findall(".//s3:Contents", ns) or root.findall(".//Contents")

    assert len(contents) == 3, f"Prefix filtering failed, found {len(contents)}"
    for content in contents:
        key_elem = content.find("s3:Key", ns)
        if key_elem is None:
            key_elem = content.find("Key")
        key = key_elem.text if key_elem is not None else ""
        assert key.startswith("folder/"), f"Prefix filter not working for {key}"

    print("  Prefix filtering works correctly")

def test_tagging():
    """Test object tagging operations."""
    print("Testing object tagging...")

    client = create_test_client()
    key = "tagged-file.txt"

    # Create object
    response = client.put(
        f"/{BUCKET}/{key}",
        content=b"Tagged content",
        headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 200

    # Set tags
    tagging_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Tagging>
    <TagSet>
        <Tag>
            <Key>Environment</Key>
            <Value>Test</Value>
        </Tag>
        <Tag>
            <Key>Type</Key>
            <Value>SmokeTest</Value>
        </Tag>
    </TagSet>
</Tagging>"""

    response = client.put(
        f"/{BUCKET}/{key}",
        content=tagging_xml,
        headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 200

    # Get tags
    response = client.get(f"/{BUCKET}/{key}", params={"tagging": ""})
    assert response.status_code == 200

    # Parse XML response
    import xml.etree.ElementTree as ET
    root = ET.fromstring(response.text)
    tag_elements = root.findall(".//Tag")

    retrieved_tags = {}
    for tag_elem in tag_elements:
        key_elem = tag_elem.find("Key")
        value_elem = tag_elem.find("Value")
        if key_elem is not None and value_elem is not None:
            retrieved_tags[key_elem.text] = value_elem.text

    expected_tags = {"Environment": "Test", "Type": "SmokeTest"}

    assert retrieved_tags == expected_tags, f"Tags mismatch: {retrieved_tags} != {expected_tags}"

    print("  Tagging operations successful")

def test_delete_object():
    """Test DELETE object operation."""
    print("Testing DELETE object...")

    client = create_test_client()
    key = "to-delete.txt"

    # Create object
    response = client.put(
        f"/{BUCKET}/{key}",
        content=b"Delete me",
        headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 200

    # Verify it exists
    response = client.head(f"/{BUCKET}/{key}")
    assert response.status_code == 200

    # Delete object
    response = client.delete(f"/{BUCKET}/{key}")
    assert response.status_code == 204

    # Verify it's gone
    response = client.head(f"/{BUCKET}/{key}")
    assert response.status_code == 404

    print("  DELETE successful, object no longer exists")

def cleanup():
    """Clean up test objects."""
    print("Cleaning up test objects...")

    client = create_test_client()

    # List all objects
    response = client.get(f"/{BUCKET}", params={"list-type": "2"})
    if response.status_code == 200:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        contents = root.findall(".//s3:Contents", ns) or root.findall(".//Contents")

        for content in contents:
            key_elem = content.find("s3:Key", ns)
            if key_elem is None:
                key_elem = content.find("Key")
            if key_elem is not None and key_elem.text:
                key = key_elem.text
                client.delete(f"/{BUCKET}/{key}")
                print(f"  Deleted {key}")

def main():
    """Run all smoke tests."""
    print("S3 Mock Server Smoke Test")
    print("=" * 40)

    try:
        # Test all operations
        test_put_get_object()
        test_head_object()
        test_list_objects()
        test_tagging()
        test_delete_object()

        print("\n" + "=" * 40)
        print("All tests passed!")

    except Exception as e:
        print(f"\nERROR: Test failed: {e}")
        raise
    finally:
        # Always cleanup
        try:
            cleanup()
        except Exception as e:
            print(f"Warning: Cleanup failed: {e}")

if __name__ == "__main__":
    main()
