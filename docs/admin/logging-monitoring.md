# Logging and Monitoring

Last updated: 2026-02-21

The application produces structured logs in JSON Lines format (`.jsonl`), which makes them easy to parse and analyze.

## The `app.jsonl` File

All application events, errors, and important information are written to a single log file named `app.jsonl`. This file is the primary source for debugging issues and monitoring the application's health. You can view the contents of this file directly from the **Admin Panel**.

## Configuring the Log Directory

It is essential to configure the location where the `app.jsonl` file is stored, especially in a production environment.

*   **Configuration**: Set the `APP_LOG_DIR` variable in your `.env` file.
*   **Example**:
    ```
    APP_LOG_DIR=/var/logs/atlas-ui
    ```
*   **Default**: If this variable is not set, the application will attempt to create a `logs` directory in the project's root, which may not be desirable or possible in a production deployment. Ensure the specified directory exists and the application has the necessary permissions to write to it.

## Configuring Log Levels

The application supports configurable log levels to control the verbosity of logging and the inclusion of sensitive data in logs.

### Setting the Log Level

Configure the log level in your `.env` file:

```bash
LOG_LEVEL=INFO
```

### Available Log Levels

The application supports the following log levels (in order of increasing severity):

*   **`DEBUG`**: Verbose logging for development and testing environments. **Includes sensitive data such as user message content and LLM responses.** This level should **never be used in production** as it logs exact user input and output.

*   **`INFO`** (Recommended for Production): Standard operational logging that tracks requests, operations, and performance metrics without exposing sensitive content. Logs include:
    - Session creation and management
    - Message counts and character counts
    - Model selection and tool usage
    - User email addresses (sanitized)
    - Response lengths and timing
    
    **Does not include:** User message content, LLM response previews, tool argument values (including tool approval payloads), or other potentially sensitive data.

*   **`WARNING`**: Logs only warnings and errors. Use for production environments where minimal logging is required.

*   **`ERROR`**: Logs only errors and critical issues.

*   **`CRITICAL`**: Logs only critical system failures.

### Production Recommendations

For production deployments:

1.  **Set `LOG_LEVEL=INFO` or higher** to prevent logging of sensitive user data
2.  Assume all user input and LLM responses contain sensitive information
3.  Only use `DEBUG` level in isolated testing/QA environments
4.  Regularly audit logs to ensure no sensitive data is being captured

### Development and Testing

For development or QA environments where verbose logging is needed:

*   Set `LOG_LEVEL=DEBUG` to capture detailed information including message content
*   Be aware that logs will contain sensitive data and should be handled accordingly
*   Never share DEBUG-level logs without first sanitizing sensitive content

## Health Monitoring

The application provides two public endpoints for monitoring, both unauthenticated but rate-limited:

### Heartbeat (`GET /api/heartbeat`)

A minimal endpoint that returns `{"status": "ok"}`. Use this for high-frequency uptime polling by load balancers, Kubernetes liveness probes, or external monitoring services. It has no dependencies and the smallest possible response payload.

### Health Check (`GET /api/health`)

Returns a richer JSON response containing the service status, name, version, and current timestamp in ISO-8601 format. Use this for readiness probes or dashboards that need version and timestamp information.

You can integrate either endpoint into your monitoring infrastructure (Kubernetes liveness/readiness probes, AWS ELB health checks, Prometheus monitoring). Neither endpoint checks database connectivity or external dependencies.

For more detailed system status information that includes configuration and component health, admin users can access the `/admin/system-status` endpoint, which requires authentication and admin group membership.
