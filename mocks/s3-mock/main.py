import os
import urllib.parse
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn

from storage import (
    ensure_bucket, load_object, save_object, delete_object,
    list_objects, get_tags, set_tags, load_meta
)
from s3_xml import (
    create_list_objects_xml, create_tagging_xml, parse_tagging_xml, create_error_xml
)

app = FastAPI(title="S3 Mock Server")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log when the S3 mock is hit."""
    print(f"S3 Mock hit: {request.method} {request.url.path}")
    response = await call_next(request)
    return response


def get_app():
    """Get the FastAPI app instance for testing."""
    return app

# Configuration
MOCK_S3_ROOT = Path(os.getenv("MOCK_S3_ROOT", "minio-data/chatui"))


def get_bucket_root(bucket: str) -> Path:
    """Get the root path for a bucket."""
    return ensure_bucket(MOCK_S3_ROOT, bucket)


def extract_metadata(headers: Dict[str, str]) -> Dict[str, str]:
    """Extract x-amz-meta-* headers."""
    metadata = {}
    for key, value in headers.items():
        if key.lower().startswith("x-amz-meta-"):
            meta_key = key[11:].lower()  # Remove "x-amz-meta-" prefix
            metadata[meta_key] = value
    return metadata


def add_metadata_headers(response: Response, metadata: Dict[str, str]):
    """Add metadata headers to response."""
    for key, value in metadata.items():
        response.headers[f"x-amz-meta-{key}"] = value


@app.put("/{bucket}/{key:path}")
async def put_object(bucket: str, key: str, request: Request):
    """PUT Object endpoint."""
    # Check if this is a tagging request with XML body (value may be empty, so check key presence)
    if "tagging" in request.query_params and request.headers.get("content-type") == "application/xml":
        bucket_root = get_bucket_root(bucket)

        # Check if object exists
        if load_meta(bucket_root, key) is None:
            error_xml = create_error_xml("NoSuchKey", "The specified key does not exist.", f"/{bucket}/{key}")
            raise HTTPException(status_code=404, detail=error_xml)

        try:
            body = await request.body()
            xml_content = body.decode("utf-8")
            tags = parse_tagging_xml(xml_content)
            set_tags(bucket_root, key, tags)
            return Response(status_code=200)
        except ValueError:
            error_xml = create_error_xml("MalformedXML", "The XML you provided was not well-formed or did not validate against the published schema.", f"/{bucket}/{key}?tagging")
            raise HTTPException(status_code=400, detail=error_xml)

    # Regular object PUT
    try:
        bucket_root = get_bucket_root(bucket)
        body = await request.body()

        # Extract content type
        content_type = request.headers.get("content-type", "application/octet-stream")

        # Extract metadata
        metadata = extract_metadata(dict(request.headers))

        # Extract tagging from query params
        tags = {}
        tagging_param = request.query_params.get("tagging")
        if tagging_param:
            # Parse URL-encoded tags: key1=value1&key2=value2
            tag_pairs = urllib.parse.parse_qs(tagging_param, keep_blank_values=True)
            for tag_key, tag_values in tag_pairs.items():
                if tag_values:
                    tags[tag_key] = tag_values[0]

        # Save object
        meta = save_object(bucket_root, key, body, content_type, metadata, tags)

        # Return response with ETag
        response = Response(status_code=200)
        response.headers["ETag"] = f'"{meta["etag"]}"'
        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/{bucket}/{key:path}")
async def get_object(bucket: str, key: str, request: Request):
    """GET Object endpoint."""
    bucket_root = get_bucket_root(bucket)

    # Check if this is a tagging request (value may be empty string, so check key presence)
    if "tagging" in request.query_params:
        # Check if object exists
        if load_meta(bucket_root, key) is None:
            error_xml = create_error_xml("NoSuchKey", "The specified key does not exist.", f"/{bucket}/{key}")
            raise HTTPException(status_code=404, detail=error_xml, media_type="application/xml")

        tags = get_tags(bucket_root, key)
        xml_response = create_tagging_xml(tags)
        return Response(content=xml_response, media_type="application/xml")

    # Regular object GET
    result = load_object(bucket_root, key)
    if result is None:
        error_xml = create_error_xml("NoSuchKey", "The specified key does not exist.", f"/{bucket}/{key}")
        raise HTTPException(status_code=404, detail=error_xml)

    data, meta = result

    # Create streaming response
    def iter_data():
        yield data

    response = StreamingResponse(iter_data(), media_type=meta.get("content_type", "application/octet-stream"))
    response.headers["ETag"] = f'"{meta["etag"]}"'
    response.headers["Content-Type"] = meta.get("content_type", "application/octet-stream")

    # Add metadata headers
    add_metadata_headers(response, meta.get("metadata", {}))

    return response


@app.head("/{bucket}/{key:path}")
async def head_object(bucket: str, key: str):
    """HEAD Object endpoint."""
    bucket_root = get_bucket_root(bucket)
    result = load_object(bucket_root, key)
    if result is None:
        error_xml = create_error_xml("NoSuchKey", "The specified key does not exist.", f"/{bucket}/{key}")
        raise HTTPException(status_code=404, detail=error_xml)

    data, meta = result

    response = Response(status_code=200)
    response.headers["ETag"] = f'"{meta["etag"]}"'
    response.headers["Content-Type"] = meta.get("content_type", "application/octet-stream")

    # Add metadata headers
    add_metadata_headers(response, meta.get("metadata", {}))

    return response


@app.delete("/{bucket}/{key:path}")
async def delete_object_endpoint(bucket: str, key: str):
    """DELETE Object endpoint."""
    bucket_root = get_bucket_root(bucket)
    deleted = delete_object(bucket_root, key)
    if not deleted:
        error_xml = create_error_xml("NoSuchKey", "The specified key does not exist.", f"/{bucket}/{key}")
        raise HTTPException(status_code=404, detail=error_xml)

    return Response(status_code=204)


@app.get("/{bucket}")
async def list_objects_v2(bucket: str, request: Request):
    """List Objects V2 endpoint."""
    bucket_root = get_bucket_root(bucket)

    # Check if bucket exists (has any objects)
    if not any(bucket_root.rglob("*")):
        error_xml = create_error_xml("NoSuchBucket", "The specified bucket does not exist.", f"/{bucket}")
        raise HTTPException(status_code=404, detail=error_xml)

    prefix = request.query_params.get("prefix", "")
    max_keys = int(request.query_params.get("max-keys", "1000"))

    objects = list_objects(bucket_root, prefix, max_keys)
    xml_response = create_list_objects_xml(bucket, prefix, objects)

    return Response(content=xml_response, media_type="application/xml")



@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with XML error responses."""
    if exc.detail and exc.detail.startswith("<Error>"):
        return Response(content=exc.detail, media_type="application/xml", status_code=exc.status_code)
    return Response(content=str(exc.detail), status_code=exc.status_code)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
