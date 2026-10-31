# File Storage and Tool Integration

Last updated: 2026-10-31

The application uses S3-compatible object storage for handling all user-uploaded files. This system is designed to be secure and flexible, allowing tools to access files without ever needing direct S3 credentials.

## Configuration Modes

You can configure the file storage in one of two modes using the `.env` file.

### 1. Development Mode (Mock S3)
For local development and testing, you can use a built-in mock S3 service.

*   **Setting**: `USE_MOCK_S3=true`
*   **Behavior**: Files are stored on the local filesystem in the `minio-data/` directory. This mode is convenient as it requires no external services or credentials.
*   **Use Case**: Ideal for local development. **This must not be used in production.**

### 2. Production Mode (Real S3)
For production, you must connect to a real S3-compatible object store like AWS S3, MinIO, or another provider.

*   **Setting**: `USE_MOCK_S3=false`
*   **Configuration**: You must provide the connection details in your `.env` file:
    ```
    S3_ENDPOINT_URL=https://your-s3-provider.com
    S3_BUCKET_NAME=your-bucket-name
    S3_ACCESS_KEY=your-access-key
    S3_SECRET_KEY=your-secret-key
    S3_REGION=us-east-1
    ```

#### Credentials

`S3_ACCESS_KEY` and `S3_SECRET_KEY` are optional. Supply them for MinIO or any
provider that issues static keys.

Omit both (or leave them empty) to use AWS-native credentials instead: boto3
then resolves them through its standard chain -- environment variables, shared
credential/config files, container credentials (ECS task roles, EKS Pod Identity
and IRSA), and finally the EC2 instance metadata service. This is the
recommended setup when running on AWS, since no long-lived secret has to be
stored in the environment.

Both values must be set together. If only one is provided it is ignored, a
warning is logged, and the default credential chain is used.

#### IAM permissions

The backend calls these S3 APIs, so the credentials in `.env` need a policy
that allows them:

| Action | Used for |
|---|---|
| `s3:PutObject` | uploads |
| `s3:PutObjectTagging` | uploads -- every object is written with tags (`source`, `user_email`, `original_filename`), and AWS S3 checks this permission for the tags that ride along on the upload request, so a policy without it fails *every* upload with `AccessDenied` |
| `s3:GetObject` | downloads |
| `s3:GetObjectTagging` | reading the tags of a stored object -- without it the file is still served, but its tags come back empty |
| `s3:ListBucket` | `GET /api/files` |
| `s3:DeleteObject` | `DELETE /api/files/{key}` |

The tagging permission is separate from `s3:PutObject` even though the tags
are written in the same request: a policy that grants only `s3:PutObject` is
enough to store the bytes, and not enough to store them with their tags.

#### File metadata in the API

`POST /api/files`, `GET /api/files` and `GET /api/files/{key}` report
`last_modified` as an ISO-8601 string (for example
`2026-10-21T12:30:45+00:00`). The S3 client libraries hand the timestamp back
as a `datetime`, and the API layer converts it; clients can parse it with any
ISO-8601 parser. The field is `null` when the storage backend cannot report
a timestamp, so clients must treat a missing value as "unknown" rather than
as the epoch.

#### Listing a page

`GET /api/files` still returns a JSON list. These query parameters narrow it:

| Parameter | Meaning |
| --- | --- |
| `limit` | Page size, from 1 to 1000. Default 100. |
| `offset` | How many matches to skip. Default 0. |
| `file_type` | `user` for uploads, or `tool` for generated files. |
| `category` | `image`, `code`, `data`, `document`, or `other`. Anything else is HTTP 400. |
| `q` | Case-insensitive substring of the stored file name. At most 200 characters. |

The response header `X-Total-Count` is the number of matches before `offset`
and `limit`. The File Library asks for 50 rows at a time and reads that
header for the pager. Keys are listed first and ordered newest-first; object
tags and metadata are read only for the page that is returned. A file with no
usable timestamp sorts after every dated file.

## How MCP Tools Access Files

The application uses a secure workflow that prevents MCP tools from needing direct access to S3 credentials. Instead, the backend acts as a proxy.

```
1. User uploads file
   [User] -> [Atlas UI Backend] -> [S3 Bucket]
                 |
                 | 2. LLM calls tool with filename
                 v
4. Tool downloads file from Atlas UI API
   [MCP Tool] <- [Atlas UI Backend] <- [S3 Bucket]
                  ^
                  | 3. Backend creates temporary, secure URL
```

1.  **File Upload**: A user uploads a file, which is stored in the configured S3 bucket.
2.  **Tool Call**: The LLM decides to use a tool that needs the file and passes the `filename` as an argument.
3.  **Secure URL Generation**: The Atlas UI backend intercepts the tool call. It generates a temporary, secure URL that points back to its own API (e.g., `/api/files/download/...`). This URL contains a short-lived capability token that grants access only to that specific file.
4.  **Tool Execution**: The backend replaces the original `filename` argument with this new secure URL and sends it to the MCP tool. The tool can then make a simple `GET` request to the URL to download the file content.

This process ensures that MCP tools can access the files they need without ever handling sensitive S3 credentials, enhancing the overall security of the system.
