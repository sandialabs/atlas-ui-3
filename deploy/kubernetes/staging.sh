#!/usr/bin/env bash
# Reproducible multi-replica staging deployment of Atlas (issue #1129).
#
# Every command that touches a cluster takes the kube context from
# ATLAS_KUBE_CONTEXT and refuses to run without it, so the current context of
# whoever runs this never decides where it lands. Full runbook:
# docs/admin/kubernetes-staging.md.
#
#   staging.sh init-secrets                 write overlays/staging/secrets.env (random values)
#   staging.sh render                       print the manifests (offline, no cluster)
#   staging.sh apply                        deploy/upgrade: image, replicas, wait for rollout
#   staging.sh scale N                      apply with N API replicas
#   staging.sh restart                      rolling restart of the API, wait for it
#   staging.sh status                       pods, endpoints and recent events
#   staging.sh validate                     validate_replicas.py against every API pod
#   staging.sh failover-drill               delete a pod under a live socket, resume elsewhere
#   staging.sh stamp-head --yes             record a create_all-built schema as current (one-off pod)
#   staging.sh teardown [--purge]           delete everything but the data volumes (--purge: those too)
#
# Environment:
#   ATLAS_KUBE_CONTEXT  kube context to use (required for cluster commands)
#   ATLAS_IMAGE         image to deploy, repo:tag or repo@sha256:... (apply/scale)
#   ATLAS_NAMESPACE     namespace (default atlas-staging)
#   ATLAS_REPLICAS      API replicas for apply (default 3)
#   ATLAS_FLAVOR        kubernetes (default) or openshift
#   KUBECTL             kubectl or oc (default kubectl)
#   PYTHON              interpreter with httpx and websockets (default: repo .venv, else python3)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
SECRETS_FILE="$HERE/overlays/staging/secrets.env"
NAMESPACE="${ATLAS_NAMESPACE:-atlas-staging}"
REPLICAS="${ATLAS_REPLICAS:-3}"
FLAVOR="${ATLAS_FLAVOR:-kubernetes}"
KUBECTL="${KUBECTL:-kubectl}"
if [ -z "${PYTHON:-}" ]; then
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PYTHON="$REPO_ROOT/.venv/bin/python"; else PYTHON=python3; fi
fi

die() { echo "staging.sh: $*" >&2; exit 1; }

# Everything started or created along the way is cleaned up on exit.
PF_PIDS=()
TMP_PATHS=()
cleanup() {
    local pid path
    for pid in "${PF_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    for path in "${TMP_PATHS[@]}"; do rm -rf "$path"; done
}
trap cleanup EXIT

new_tmp() {
    local kind="$1" path
    # Build dirs live beside the overlays: kustomize only accepts relative
    # resource paths. They are git-ignored and removed on exit.
    if [ "$kind" = dir ]; then path="$(mktemp -d "$HERE/.build-XXXXXX")"; else path="$(mktemp)"; fi
    TMP_PATHS+=("$path")
    TMP="$path"
}

overlay_dir() {
    case "$FLAVOR" in
        kubernetes) echo "../overlays/staging" ;;
        openshift) echo "../overlays/staging-openshift" ;;
        *) die "ATLAS_FLAVOR must be kubernetes or openshift, not '$FLAVOR'" ;;
    esac
}

kc() {
    [ -n "${ATLAS_KUBE_CONTEXT:-}" ] || die "set ATLAS_KUBE_CONTEXT to the staging cluster's kube context"
    "$KUBECTL" --context "$ATLAS_KUBE_CONTEXT" --namespace "$NAMESPACE" "$@"
}

secret_value() {
    [ -f "$SECRETS_FILE" ] || die "$SECRETS_FILE is missing; run: staging.sh init-secrets"
    sed -n "s/^$1=//p" "$SECRETS_FILE" | tail -n 1
}

# A throwaway kustomization layered over the overlay: it pins the image, the
# replica count and the namespace without editing tracked files.
build_dir() {
    local dir="$1"
    [ -n "${ATLAS_IMAGE:-}" ] || die "set ATLAS_IMAGE to an exact image (repo:tag or repo@sha256:...)"
    [[ "$REPLICAS" =~ ^[1-9][0-9]*$ ]] || die "ATLAS_REPLICAS must be a positive integer"
    local image_line
    if [[ "$ATLAS_IMAGE" == *@sha256:* ]]; then
        image_line="    newName: ${ATLAS_IMAGE%@*}
    digest: ${ATLAS_IMAGE#*@}"
    else
        local tag="${ATLAS_IMAGE##*:}"
        [[ "$ATLAS_IMAGE" == *:* && "$tag" != */* ]] || die "ATLAS_IMAGE needs an explicit tag or digest"
        [ "$tag" != "latest" ] || die "ATLAS_IMAGE must not use :latest (rollouts would not be reproducible)"
        image_line="    newName: ${ATLAS_IMAGE%:*}
    newTag: $tag"
    fi
    cat > "$dir/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $NAMESPACE
resources:
  - $(overlay_dir)
images:
  - name: atlas-ui-3
$image_line
replicas:
  - name: atlas-api
    count: $REPLICAS
EOF
}

cmd_init_secrets() {
    [ ! -e "$SECRETS_FILE" ] || die "$SECRETS_FILE already exists. New values do not rotate a deployed
PostgreSQL password: the kept volume (data-atlas-postgres-0) still holds the old one, and every pod would
stop in Init with 'PostgreSQL rejected the credentials'. To start over, run 'teardown --purge' first; to
rotate in place, change the role password in PostgreSQL (ALTER ROLE) and edit secrets.env to match."
    local pg redis s3_user s3_key
    pg="$(openssl rand -hex 24)"; redis="$(openssl rand -hex 24)"
    s3_user="atlas$(openssl rand -hex 4)"; s3_key="$(openssl rand -hex 24)"
    umask 077
    cat > "$SECRETS_FILE" <<EOF
POSTGRES_PASSWORD=$pg
REDIS_PASSWORD=$redis
CHAT_HISTORY_DB_URL=postgresql://atlas:$pg@atlas-postgres:5432/atlas_chat_history
REDIS_URL=redis://:$redis@atlas-redis:6379/0
S3_ENDPOINT=http://atlas-minio:9000
S3_ACCESS_KEY=$s3_user
S3_SECRET_KEY=$s3_key
PROXY_SECRET=$(openssl rand -base64 48 | tr -d '\n')
MCP_TOKEN_ENCRYPTION_KEY=$(openssl rand -base64 48 | tr -d '\n')
CAPABILITY_TOKEN_SECRET=$(openssl rand -base64 48 | tr -d '\n')
EOF
    echo "wrote $SECRETS_FILE (mode 600, git-ignored)"
}

cmd_render() {
    new_tmp dir; build_dir "$TMP"
    "$KUBECTL" kustomize "$TMP"
}
wait_rollout() {
    kc rollout status statefulset/atlas-postgres --timeout=300s
    kc rollout status deployment/atlas-redis --timeout=300s
    kc rollout status statefulset/atlas-minio --timeout=300s
    kc wait --for=condition=complete job/atlas-minio-bucket --timeout=600s
    kc rollout status deployment/atlas-api --timeout=900s
}

# Generated ConfigMaps and Secrets are hash-suffixed, so every change leaves
# the previous one behind (a superseded Secret still holds live credentials).
# Delete those of this instance that the current render no longer names.
prune_generated() {
    local dir="$1" keep kind name
    keep="$("$KUBECTL" kustomize "$dir" | "$PYTHON" -c '
import sys, yaml
for doc in yaml.safe_load_all(sys.stdin):
    if doc and doc["kind"] in ("ConfigMap", "Secret"):
        print(doc["kind"].lower() + "/" + doc["metadata"]["name"])')"
    while read -r kind name; do
        [ -n "$name" ] || continue
        if ! grep -qx "$kind/$name" <<<"$keep"; then
            kc delete "$kind" "$name" --ignore-not-found >/dev/null && echo "pruned superseded $kind/$name"
        fi
    done < <(kc get configmap,secret -l app.kubernetes.io/instance=atlas-staging \
        -o jsonpath='{range .items[*]}{.kind}{" "}{.metadata.name}{"\n"}{end}' | awk '{print tolower($1), $2}')
}

# Create the namespace only when the cluster says it does not exist: a user
# limited to one namespace gets Forbidden on reading it, and that namespace
# exists by definition.
ensure_namespace() {
    local out
    if out="$("$KUBECTL" --context "$ATLAS_KUBE_CONTEXT" get namespace "$NAMESPACE" 2>&1)"; then
        return 0
    fi
    if ! grep -q "NotFound" <<<"$out"; then
        echo "staging.sh: cannot read namespace $NAMESPACE (${out%%$'\n'*}); assuming it exists" >&2
        return 0
    fi
    if [ "$FLAVOR" = openshift ]; then
        "$KUBECTL" --context "$ATLAS_KUBE_CONTEXT" new-project "$NAMESPACE" --skip-config-write >/dev/null \
            || die "could not create project $NAMESPACE; ask an administrator to create it"
    else
        "$KUBECTL" --context "$ATLAS_KUBE_CONTEXT" create namespace "$NAMESPACE"
    fi
}

cmd_apply() {
    local dir
    new_tmp dir; dir="$TMP"
    build_dir "$dir"
    [ -f "$SECRETS_FILE" ] || die "$SECRETS_FILE is missing; run: staging.sh init-secrets"
    ensure_namespace
    # A Job's pod template is immutable and names the generated Secret and
    # ConfigMap; the bucket Job is idempotent, so replace it on every apply.
    kc delete job atlas-minio-bucket --ignore-not-found --wait=true >/dev/null
    kc apply -k "$dir"
    wait_rollout
    prune_generated "$dir"
    cmd_status
}

cmd_scale() {
    [ $# -eq 1 ] || die "usage: staging.sh scale N"
    REPLICAS="$1" cmd_apply
}

cmd_restart() {
    kc rollout restart deployment/atlas-api
    kc rollout status deployment/atlas-api --timeout=900s
    cmd_status
}

cmd_status() {
    kc get deployment,statefulset,pods -o wide
    kc get endpointslices -l kubernetes.io/service-name=atlas-api
    kc get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 15 || true
}

# Validation describes the deployment only when every desired replica is
# ready: a pass with two of three pods up would hide the third's failure.
require_all_ready() {
    local ready="$1" desired
    desired="$(kc get deployment atlas-api -o jsonpath='{.spec.replicas}')"
    [ -n "$desired" ] || die "could not read spec.replicas of deployment/atlas-api"
    [ "$desired" -ge 2 ] || die "deployment/atlas-api wants $desired replica(s); cross-replica checks need at least 2"
    [ "$ready" -eq "$desired" ] || die "only $ready of $desired atlas-api pods are ready; see 'staging.sh status'"
}

# Ready API pods that are not terminating (a deleted pod can still report
# ready during its grace period).
ready_api_pods() {
    kc get pods -l app.kubernetes.io/name=atlas-api -o json | "$PYTHON" -c '
import json, sys
for pod in json.load(sys.stdin)["items"]:
    statuses = pod.get("status", {}).get("containerStatuses") or []
    if pod["metadata"].get("deletionTimestamp") or not statuses or not all(c.get("ready") for c in statuses):
        continue
    print(pod["metadata"]["name"])'
}

# port_forward POD: tunnels a free local port to the pod and sets PF_URL once
# it answers. Runs in this shell (not a $(...) subshell) so cleanup can stop
# the tunnel.
port_forward() {
    local pod="$1" port
    port="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
    new_tmp file
    kc port-forward "pod/$pod" "$port:8000" >"$TMP" 2>&1 &
    PF_PIDS+=("$!")
    local i
    for i in $(seq 1 60); do
        if curl -fsS "http://127.0.0.1:$port/api/heartbeat" >/dev/null 2>&1; then
            PF_URL="http://127.0.0.1:$port"
            return 0
        fi
        sleep 0.5
    done
    cat "$TMP" >&2
    die "port-forward to $pod did not come up"
}

cmd_validate() {
    local pods=() args=() pod
    mapfile -t pods < <(ready_api_pods)
    require_all_ready "${#pods[@]}"
    echo "Validating ${#pods[@]} ready API pods: ${pods[*]}"
    for pod in "${pods[@]}"; do
        port_forward "$pod"
        args+=("--replica=$pod=$PF_URL")
    done
    ATLAS_PROXY_SECRET="$(secret_value PROXY_SECRET)" "$PYTHON" "$HERE/validate_replicas.py" all "${args[@]}"
}

cmd_failover_drill() {
    local pods=() victim survivor victim_url survivor_url user holder_out session_id
    mapfile -t pods < <(ready_api_pods)
    require_all_ready "${#pods[@]}"
    victim="${pods[0]}"; survivor="${pods[1]}"
    port_forward "$victim"; victim_url="$PF_URL"
    port_forward "$survivor"; survivor_url="$PF_URL"
    user="drill-$(openssl rand -hex 4)@example.com"
    new_tmp file; holder_out="$TMP"
    export ATLAS_PROXY_SECRET; ATLAS_PROXY_SECRET="$(secret_value PROXY_SECRET)"
    "$PYTHON" "$HERE/validate_replicas.py" hold-session "--replica=$victim=$victim_url" "--user=$user" >"$holder_out" 2>&1 &
    local holder=$!
    for _ in $(seq 1 40); do [ -s "$holder_out" ] && break; sleep 0.5; done
    session_id="$(head -n 1 "$holder_out")"
    [[ "$session_id" =~ ^[0-9a-f-]{36}$ ]] || { cat "$holder_out" >&2; die "could not open a session on $victim"; }
    echo "Session $session_id is live on $victim; deleting that pod (graceful, like a rollout or drain)"
    kc delete pod "$victim" --wait=true
    wait "$holder" || true
    echo "Client saw: $(tail -n 1 "$holder_out")"
    "$PYTHON" "$HERE/validate_replicas.py" resume-session "--replica=$survivor=$survivor_url" \
        "--user=$user" "--session-id=$session_id"
    kc rollout status deployment/atlas-api --timeout=900s
}

# A database built by an Atlas startup (create_all) has no alembic_version, so
# every pod's migrate init container refuses it and no API pod runs to exec
# into. Stamp it from a one-off pod with the Deployment's own image and Secret.
cmd_stamp_head() {
    [ "${1:-}" = "--yes" ] || die "stamp-head records the CURRENT schema as the image's Alembic head without migrating it. Check the schema matches this release first, then run: staging.sh stamp-head --yes"
    local image secret overrides
    image="$(kc get deployment atlas-api -o jsonpath='{.spec.template.spec.initContainers[0].image}')"
    secret="$(kc get deployment atlas-api \
        -o jsonpath='{.spec.template.spec.initContainers[0].env[?(@.name=="CHAT_HISTORY_DB_URL")].valueFrom.secretKeyRef.name}')"
    [ -n "$image" ] && [ -n "$secret" ] || die "could not read the image and Secret from deployment/atlas-api"
    overrides="$("$PYTHON" - "$image" "$secret" <<'PY'
import json, sys
image, secret = sys.argv[1:3]
print(json.dumps({"spec": {
    "automountServiceAccountToken": False,
    "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
    "containers": [{
        "name": "atlas-stamp-head",
        "image": image,
        "command": ["alembic", "-c", "/app/alembic.ini", "stamp", "head"],
        "env": [{"name": "CHAT_HISTORY_DB_URL",
                 "valueFrom": {"secretKeyRef": {"name": secret, "key": "CHAT_HISTORY_DB_URL"}}}],
        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                      "limits": {"cpu": "1", "memory": "512Mi"}},
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
    }],
}}))
PY
)"
    kc run atlas-stamp-head --rm -i --restart=Never --image "$image" --overrides "$overrides"
    echo "Stamped. Pods held in Init retry their migrate container and continue."
}

cmd_teardown() {
    local dir
    new_tmp dir; dir="$TMP"
    ATLAS_IMAGE="${ATLAS_IMAGE:-atlas-ui-3:teardown}" build_dir "$dir"
    [ -f "$SECRETS_FILE" ] || die "$SECRETS_FILE is missing (needed to render what to delete)"
    kc delete -k "$dir" --ignore-not-found --wait=true
    # Earlier generations of the hash-suffixed ConfigMap and Secret too.
    kc delete configmap,secret -l app.kubernetes.io/instance=atlas-staging --ignore-not-found
    # StatefulSet claims outlive `delete -k`; their names are fixed by the
    # claim template, StatefulSet name and ordinal.
    local claims=(data-atlas-postgres-0 data-atlas-minio-0)
    if [ "${1:-}" = "--purge" ]; then
        kc delete pvc "${claims[@]}" --ignore-not-found
    else
        echo "Kept the PostgreSQL and MinIO volumes (${claims[*]}): chat history and"
        echo "attachments survive a redeploy. 'staging.sh teardown --purge' deletes them."
    fi
}

main() {
    local command="${1:-}"; shift || true
    case "$command" in
        init-secrets) cmd_init_secrets "$@" ;;
        render) cmd_render "$@" ;;
        apply) cmd_apply "$@" ;;
        scale) cmd_scale "$@" ;;
        restart) cmd_restart "$@" ;;
        status) cmd_status "$@" ;;
        validate) cmd_validate "$@" ;;
        failover-drill) cmd_failover_drill "$@" ;;
        stamp-head) cmd_stamp_head "$@" ;;
        teardown) cmd_teardown "$@" ;;
        *) sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; [ -z "$command" ] || exit 2 ;;
    esac
}

main "$@"
