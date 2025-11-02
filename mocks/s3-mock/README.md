# S3 Mock Server

A minimal FastAPI server that mimics S3's REST API for development and testing purposes. Compatible with botocore/boto3 expectations.

## Features

- **Path-style addressing**: `http://localhost:9001/{bucket}/{key}`
- **S3v4 signed requests**: Accepts but doesn't validate signatures
- **Persistent storage**: Data survives server restarts
- **XML responses**: Compatible with botocore parsing
- **Minimal S3 operations**: PUT, GET, HEAD, DELETE, ListObjectsV2, Tagging

## Supported Operations

### Object Operations
- `PUT /{bucket}/{key}` - Upload object with optional metadata and tagging
- `GET /{bucket}/{key}` - Download object
- `HEAD /{bucket}/{key}` - Get object metadata
- `DELETE /{bucket}/{key}` - Delete object

### Listing Operations
- `GET /{bucket}?list-type=2&prefix=...` - List objects V2

### Tagging Operations
- `GET /{bucket}/{key}?tagging` - Get object tags
- `PUT /{bucket}/{key}?tagging` - Set object tags

## Storage

- **Root directory**: `minio-data/chatui/` (configurable via `MOCK_S3_ROOT`)
- **Bucket structure**: `{root}/{bucket}/`
- **Object files**: `{bucket}/{key}` (data)
- **Metadata files**: `{bucket}/{key}.meta.json` (content-type, etag, metadata, tags)
- **Tag files**: Tags stored in metadata JSON

## Running the Server

### Prerequisites
```bash
uv pip install fastapi uvicorn
```

### Start the server
```bash
cd mocks/s3-mock
python main.py
```

Server runs on `http://localhost:9001` by default. Configure port with `PORT` environment variable.

## Backend Configuration

Configure your backend to use the mock server:

```bash
# Environment variables
S3_ENDPOINT_URL=http://localhost:9001
S3_REGION=us-east-1
S3_BUCKET=atlas-files
S3_USE_SSL=false
S3_ADDRESSING_STYLE=path
S3_ACCESS_KEY_ID=any_value
S3_SECRET_ACCESS_KEY=any_value
```

## Testing

Use the included smoke test script to verify functionality:

```bash
cd mocks/s3-mock
python smoke_test.py
```

The test performs:
- PUT object with metadata and tags
- GET object and verify content/metadata/ETag
- HEAD object and verify headers
- List objects with prefix filtering
- Get/set object tagging
- DELETE object and verify cleanup

## API Details

### PUT Object
```
PUT /{bucket}/{key}
Content-Type: <mime-type>
x-amz-meta-*: <metadata>
?tagging=key1%3Dvalue1%26key2%3Dvalue2

Body: <binary data>

Response: 200 OK
ETag: "<md5hex>"
```

### GET Object
```
GET /{bucket}/{key}

Response: 200 OK
Content-Type: <mime-type>
ETag: "<md5hex>"
x-amz-meta-*: <metadata>
Body: <binary data>
```

### List Objects V2
```
GET /{bucket}?list-type=2&prefix=<prefix>&max-keys=<n>

Response: 200 OK
Content-Type: application/xml

<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>bucket</Name>
  <Prefix>prefix</Prefix>
  <KeyCount>n</KeyCount>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>object-key</Key>
    <LastModified>2025-11-02T10:00:00.000Z</LastModified>
    <ETag>"md5hex"</ETag>
    <Size>123</Size>
    <StorageClass>STANDARD</StorageClass>
  </Contents>
</ListBucketResult>
```

### Tagging
```
GET /{bucket}/{key}?tagging

Response: 200 OK
Content-Type: application/xml

<Tagging>
  <TagSet>
    <Tag>
      <Key>key1</Key>
      <Value>value1</Value>
    </Tag>
  </TagSet>
</Tagging>
```

## Error Responses

Returns S3-compatible XML error responses:

```xml
<Error>
  <Code>NoSuchKey</Code>
  <Message>The specified key does not exist.</Message>
  <Resource>/{bucket}/{key}</Resource>
</Error>
```

## Limitations

- No multipart upload support
- No versioning, ACLs, or presigned URLs
- No signature verification (accepts all auth headers)
- Virtual-hosted-style addressing not supported
- Single PUT operations only (no chunked uploads)
- Basic prefix filtering for listing (no delimiter support)

## Development

The mock server is structured as:

- `main.py` - FastAPI application and routes
- `storage.py` - Filesystem storage operations
- `s3_xml.py` - XML generation and parsing
- `README.md` - This documentation
