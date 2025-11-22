# File Storage and Tool Integration

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
