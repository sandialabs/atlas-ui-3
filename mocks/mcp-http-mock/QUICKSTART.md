# Quick Start Guide - MCP HTTP Mock Server Deployment

This is a quick reference guide for deploying the MCP HTTP Mock Server.

## Docker Quick Start

### Build the Image

Choose one of the following base images:

```bash
# Ubuntu (recommended)
docker build -f Dockerfile.ubuntu -t mcp-http-mock:ubuntu .

# Fedora
docker build -f Dockerfile.fedora -t mcp-http-mock:fedora .

# RHEL UBI
docker build -f Dockerfile.rhel -t mcp-http-mock:rhel .
```

### Run the Container

```bash
docker run -d \
  --name mcp-server \
  -p 8005:8005 \
  -e MCP_MOCK_TOKEN_1="your-token-123" \
  mcp-http-mock:ubuntu
```

### Test the Server

```bash
# Basic test
curl http://localhost:8005/mcp

# With authentication
curl -H "Authorization: Bearer your-token-123" http://localhost:8005/mcp
```

## Docker Compose Quick Start

```bash
# Start the server
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the server
docker-compose down
```

## Kubernetes/Helm Quick Start

### Prerequisites
- Kubernetes cluster running
- kubectl configured
- Helm 3.x installed
- Docker image pushed to a registry

### Install

```bash
# Basic installation
helm install mcp-server ./helm/mcp-server --namespace mcp --create-namespace

# With custom image
helm install mcp-server ./helm/mcp-server \
  --namespace mcp \
  --create-namespace \
  --set image.repository=your-registry/mcp-http-mock \
  --set image.tag=1.0.0

# Development environment
helm install mcp-server ./helm/mcp-server \
  -f ./helm/mcp-server/values-dev.yaml \
  --namespace mcp-dev \
  --create-namespace

# Production environment
helm install mcp-server ./helm/mcp-server \
  -f ./helm/mcp-server/values-prod.yaml \
  --namespace mcp-prod \
  --create-namespace
```

### Verify

```bash
# Check deployment
kubectl get all -n mcp

# Check logs
kubectl logs -n mcp deployment/mcp-server

# Test locally
kubectl port-forward -n mcp svc/mcp-server 8005:8005
curl http://localhost:8005/mcp
```

### Upgrade

```bash
helm upgrade mcp-server ./helm/mcp-server \
  --namespace mcp \
  --set image.tag=1.1.0
```

### Uninstall

```bash
helm uninstall mcp-server --namespace mcp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| MCP_MOCK_TOKEN_1 | test-api-key-123 | Primary auth token |
| MCP_MOCK_TOKEN_2 | another-test-key-456 | Secondary auth token |
| PYTHONUNBUFFERED | 1 | Python unbuffered output |

## Common Deployment Scenarios

### Single Instance (Development)

```bash
helm install mcp-dev ./helm/mcp-server \
  --set replicaCount=1 \
  --set service.type=NodePort \
  --namespace dev
```

### High Availability (Production)

```bash
helm install mcp-prod ./helm/mcp-server \
  --set replicaCount=3 \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=3 \
  --set autoscaling.maxReplicas=10 \
  --set service.type=LoadBalancer \
  --namespace prod
```

### With Ingress

```bash
helm install mcp-server ./helm/mcp-server \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=mcp.example.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix \
  --namespace mcp
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker logs mcp-server

# Or for Kubernetes
kubectl logs -n mcp deployment/mcp-server
```

### Health check failing
```bash
# Test endpoint manually
curl http://localhost:8005/mcp
```

### Image pull errors
- Verify image exists in registry
- Check imagePullSecrets configuration
- Verify network connectivity

## More Information

For detailed documentation, see [DOCKER_K8S_DEPLOYMENT.md](DOCKER_K8S_DEPLOYMENT.md)
