# Atlas on Kubernetes/OpenShift (staging)

Three Atlas API replicas on shared Redis, PostgreSQL and S3, deployed and
validated with `staging.sh` (issue #1129). Start with the runbook:
[docs/admin/kubernetes-staging.md](../../docs/admin/kubernetes-staging.md).

```bash
export ATLAS_KUBE_CONTEXT=<staging context> ATLAS_IMAGE=<registry>/atlas-ui-3:<tag>
./staging.sh init-secrets && ./staging.sh apply && ./staging.sh validate
```

- `base/`: API Deployment, Service, PDB, non-secret settings
- `overlays/staging/`: 3 replicas + staging Redis/PostgreSQL/MinIO
- `overlays/staging-openshift/`: the same, without fixed UIDs (restricted-v2 SCC)
- `validate_replicas.py`: per-replica health, auth, shared state, cross-replica session resume
- `podman_rehearsal.py`: run the rendered overlay with podman when no cluster is available
