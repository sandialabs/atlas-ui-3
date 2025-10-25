# S3 Mock Service

A lightweight mock S3 storage service for development and testing purposes.

## Features

- In-memory file storage
- User-based file isolation
- S3-compatible API endpoints
- Base64 content handling
- File tagging support
- Authorization via Bearer tokens

## API Endpoints

### Upload File
```
POST /files
Authorization: Bearer <user_email>
Content-Type: application/json

{
    "filename": "example.txt",
    "content_base64": "SGVsbG8gV29ybGQ=",
    "content_type": "text/plain",
    "tags": {
        "source": "user"
    }
}
```

### Get File
```
GET /files/{file_key}
Authorization: Bearer <user_email>
```

### List Files
```
GET /files?file_type=user&limit=50
Authorization: Bearer <user_email>
```

### Delete File
```
DELETE /files/{file_key}
Authorization: Bearer <user_email>
```

### Get File Statistics
```
GET /users/{user_email}/files/stats
Authorization: Bearer <user_email>
```

### Health Check
```
GET /health
```

## File Organization

Files are stored with keys following this pattern:
- User uploads: `users/{email}/uploads/{timestamp}_{uuid}_{filename}`
- Tool generated: `users/{email}/generated/{timestamp}_{uuid}_{filename}`

## Running the Service

```bash
cd mocks/s3-mock
python main.py
```

The service will start on `http://127.0.0.1:8003` by default.

## Environment Variables

- `HOST`: Service host (default: 127.0.0.1)
- `PORT`: Service port (default: 8003)

## Authorization

For the mock service, the Bearer token is used directly as the user email. In production, this would be replaced with proper JWT validation.

## File Types

The service supports tagging files with different types:
- `user`: User-uploaded files
- `tool`: Tool-generated files

This allows for proper categorization and different handling of files based on their source.