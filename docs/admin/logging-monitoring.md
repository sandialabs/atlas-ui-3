# Logging and Monitoring

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

## Health Monitoring

The application provides a public health check endpoint at `/api/health` specifically designed for monitoring tools, load balancers, and orchestration platforms. This endpoint requires no authentication and returns a JSON response containing the service status, version, and current timestamp in ISO-8601 format. 

You can integrate this endpoint into your monitoring infrastructure (such as Kubernetes liveness/readiness probes, AWS ELB health checks, or Prometheus monitoring) to verify that the backend service is running and responding correctly. 

The endpoint is lightweight and does not check database connectivity or external dependencies, making it ideal for high-frequency health polling without impacting application performance. 

For more detailed system status information that includes configuration and component health, admin users can access the `/admin/system-status` endpoint, which requires authentication and admin group membership.
