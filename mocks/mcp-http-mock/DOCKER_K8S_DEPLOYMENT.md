# MCP HTTP Mock Server - Docker and Kubernetes Deployment Guide

This directory contains Docker files and Kubernetes Helm charts for deploying the MCP HTTP Mock Server in containerized environments.

## Table of Contents

- [Overview](#overview)
- [Docker Deployment](#docker-deployment)
  - [Available Dockerfiles](#available-dockerfiles)
  - [Building Docker Images](#building-docker-images)
  - [Running Docker Containers](#running-docker-containers)
- [Kubernetes Deployment](#kubernetes-deployment)
  - [Prerequisites](#prerequisites)
  - [Helm Chart Installation](#helm-chart-installation)
  - [Configuration](#configuration)
  - [Deployment Examples](#deployment-examples)
- [Environment Variables](#environment-variables)
- [Health Checks](#health-checks)
- [Security Considerations](#security-considerations)

## Overview

The MCP HTTP Mock Server is a FastMCP-based HTTP server that simulates database operations for testing and demonstration purposes. This guide provides instructions for deploying the server using Docker and Kubernetes.

## Docker Deployment

### Available Dockerfiles

Two Dockerfiles are provided to support different base images:

1. **Dockerfile.ubuntu** - Ubuntu 24.04 based image (recommended for general use)
2. **Dockerfile.rhel** - Red Hat Enterprise Linux UBI 9 based image (for enterprise environments)

### Building Docker Images

#### Ubuntu-based Image

```bash
cd /path/to/atlas-ui-3/mocks/mcp-http-mock

# Build the image
docker build -f Dockerfile.ubuntu -t mcp-http-mock:ubuntu-latest .

# Or with a specific tag
docker build -f Dockerfile.ubuntu -t mcp-http-mock:ubuntu-1.0.0 .
```


#### RHEL-based Image

```bash
docker build -f Dockerfile.rhel -t mcp-http-mock:rhel-latest .
```

### Running Docker Containers

#### Basic Run

```bash
docker run -d \
  --name mcp-server \
  -p 8005:8005 \
  mcp-http-mock:ubuntu-latest
```

#### Run with Custom Authentication Tokens

```bash
docker run -d \
  --name mcp-server \
  -p 8005:8005 \
  -e MCP_MOCK_TOKEN_1="your-custom-token-123" \
  -e MCP_MOCK_TOKEN_2="another-token-456" \
  mcp-http-mock:ubuntu-latest
```

#### Run with Health Check Monitoring

```bash
docker run -d \
  --name mcp-server \
  -p 8005:8005 \
  --health-cmd "curl -f http://localhost:8005/mcp || exit 1" \
  --health-interval 30s \
  --health-timeout 10s \
  --health-retries 3 \
  mcp-http-mock:ubuntu-latest
```

#### Check Container Health

```bash
# View container health status
docker ps

# View detailed health check logs
docker inspect --format='{{json .State.Health}}' mcp-server | jq
```

### Testing the Container

```bash
# Test the MCP endpoint
curl http://localhost:8005/mcp

# Test with authentication
curl -H "Authorization: Bearer test-api-key-123" http://localhost:8005/mcp
```

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster (v1.19+)
- kubectl configured to communicate with your cluster
- Helm 3.x installed
- Container registry access (for pushing Docker images)

### Helm Chart Installation

#### Step 1: Build and Push Docker Image

```bash
# Build the image
docker build -f Dockerfile.ubuntu -t your-registry/mcp-http-mock:1.0.0 .

# Push to your container registry
docker push your-registry/mcp-http-mock:1.0.0
```

#### Step 2: Install the Helm Chart

```bash
# Navigate to the helm chart directory
cd helm/mcp-server

# Install with default values
helm install mcp-server . --namespace mcp --create-namespace

# Install with custom values
helm install mcp-server . \
  --namespace mcp \
  --create-namespace \
  --set image.repository=your-registry/mcp-http-mock \
  --set image.tag=1.0.0
```

#### Step 3: Verify Installation

```bash
# Check deployment status
kubectl get deployments -n mcp

# Check pod status
kubectl get pods -n mcp

# Check service
kubectl get svc -n mcp

# View logs
kubectl logs -n mcp deployment/mcp-server
```

### Configuration

#### Using Custom values.yaml

Create a custom `values-custom.yaml` file:

```yaml
replicaCount: 2

image:
  repository: your-registry/mcp-http-mock
  tag: "1.0.0"
  baseImage: "ubuntu"

service:
  type: LoadBalancer
  port: 8005

resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 200m
    memory: 256Mi

env:
  MCP_MOCK_TOKEN_1: "production-token-abc"
  MCP_MOCK_TOKEN_2: "production-token-xyz"

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

Install with custom values:

```bash
helm install mcp-server . -f values-custom.yaml --namespace mcp --create-namespace
```

### Deployment Examples

#### Example 1: Development Environment

```bash
helm install mcp-server-dev . \
  --namespace mcp-dev \
  --create-namespace \
  --set replicaCount=1 \
  --set service.type=NodePort \
  --set resources.limits.cpu=500m \
  --set resources.limits.memory=512Mi
```

#### Example 2: Production Environment with High Availability

```bash
helm install mcp-server-prod . \
  --namespace mcp-prod \
  --create-namespace \
  --set replicaCount=3 \
  --set service.type=LoadBalancer \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=3 \
  --set autoscaling.maxReplicas=10 \
  --set resources.limits.cpu=1000m \
  --set resources.limits.memory=1Gi \
  --set env.MCP_MOCK_TOKEN_1="prod-secure-token-123" \
  --set env.MCP_MOCK_TOKEN_2="prod-secure-token-456"
```

#### Example 3: With Ingress Enabled

```bash
helm install mcp-server . \
  --namespace mcp \
  --create-namespace \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.hosts[0].host=mcp-server.example.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

#### Example 4: Multiple Deployments (Different Configurations)

Deploy multiple MCP servers with different purposes:

```bash
# Analytics MCP server
helm install mcp-analytics . \
  --namespace mcp \
  --set nameOverride=mcp-analytics \
  --set service.port=8005 \
  --set env.MCP_MOCK_TOKEN_1="analytics-token"

# Testing MCP server
helm install mcp-testing . \
  --namespace mcp \
  --set nameOverride=mcp-testing \
  --set service.port=8006 \
  --set env.MCP_MOCK_TOKEN_1="testing-token"
```

## Environment Variables

The following environment variables can be configured:

| Variable | Description | Default Value |
|----------|-------------|---------------|
| `MCP_MOCK_TOKEN_1` | Primary authentication token | `test-api-key-123` |
| `MCP_MOCK_TOKEN_2` | Secondary authentication token | `another-test-key-456` |
| `PYTHONUNBUFFERED` | Python unbuffered mode | `1` |

## Health Checks

### Docker Health Check

The Docker images include built-in health checks:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8005/mcp || exit 1
```

### Kubernetes Probes

The Helm chart includes liveness and readiness probes:

**Liveness Probe:**
- Checks if the container is running
- Initial delay: 30 seconds
- Period: 10 seconds
- Timeout: 5 seconds
- Failure threshold: 3

**Readiness Probe:**
- Checks if the container is ready to serve traffic
- Initial delay: 10 seconds
- Period: 5 seconds
- Timeout: 3 seconds
- Failure threshold: 3

## Security Considerations

### Important Security Notes

1. **Authentication Tokens**: The default tokens are for development/testing only. Always use strong, unique tokens in production environments.

2. **Non-Root User**: All Docker images run as a non-root user (`mcpuser`) for security.

3. **Pod Security Context**: The Helm chart includes security contexts with:
   - `runAsNonRoot: true`
   - `readOnlyRootFilesystem: false` (required for application operation)
   - `allowPrivilegeEscalation: false`
   - Capabilities dropped: ALL

4. **Network Policies**: Consider enabling network policies in production:

```yaml
networkPolicy:
  enabled: true
  ingress:
    - from:
      - namespaceSelector:
          matchLabels:
            name: atlas-ui
      ports:
        - protocol: TCP
          port: 8005
```

5. **Secrets Management**: For production, use Kubernetes secrets or external secret managers:

```bash
# Create a Kubernetes secret
kubectl create secret generic mcp-tokens \
  --from-literal=token1='your-secure-token-1' \
  --from-literal=token2='your-secure-token-2' \
  -n mcp

# Reference in values.yaml
envFrom:
  - secretRef:
      name: mcp-tokens
```

## Maintenance and Operations

### Upgrading the Deployment

```bash
# Upgrade with new image version
helm upgrade mcp-server . \
  --namespace mcp \
  --set image.tag=1.1.0

# Upgrade with new values file
helm upgrade mcp-server . \
  --namespace mcp \
  -f values-custom.yaml
```

### Rollback

```bash
# View release history
helm history mcp-server -n mcp

# Rollback to previous version
helm rollback mcp-server -n mcp

# Rollback to specific revision
helm rollback mcp-server 2 -n mcp
```

### Uninstalling

```bash
# Uninstall the Helm release
helm uninstall mcp-server -n mcp

# Clean up namespace (optional)
kubectl delete namespace mcp
```

### Monitoring and Debugging

```bash
# View pod logs
kubectl logs -n mcp -l app.kubernetes.io/name=mcp-server

# Follow logs in real-time
kubectl logs -n mcp -l app.kubernetes.io/name=mcp-server -f

# Describe pod for events
kubectl describe pod -n mcp -l app.kubernetes.io/name=mcp-server

# Execute commands in the container
kubectl exec -it -n mcp deployment/mcp-server -- /bin/bash

# Port forward for local testing
kubectl port-forward -n mcp svc/mcp-server 8005:8005
```

## Troubleshooting

### Common Issues

1. **Image Pull Errors**
   - Ensure the image exists in your registry
   - Check `imagePullSecrets` if using a private registry
   - Verify network connectivity to the registry

2. **Pod CrashLoopBackOff**
   - Check logs: `kubectl logs -n mcp <pod-name>`
   - Verify environment variables are correct
   - Check resource limits

3. **Service Not Accessible**
   - Verify service type and port configuration
   - Check network policies
   - Ensure ingress is correctly configured

4. **Health Check Failures**
   - Adjust probe timing in values.yaml
   - Verify application startup time
   - Check application logs for errors

## Additional Resources

- [MCP HTTP Mock Server README](README.md)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Helm Documentation](https://helm.sh/docs/)

## Support

For issues and questions:
- GitHub Issues: https://github.com/sandialabs/atlas-ui-3/issues
- Email: support@example.com
