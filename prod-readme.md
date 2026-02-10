# ATLAS Production Setup Guide

Last updated: 2026-02-09

This guide covers how to run ATLAS on this Ubuntu machine using K3s (lightweight Kubernetes) and make it accessible to other devices on your local network (and optionally from the internet).

---

## 1. Prerequisites

- Ubuntu 24.04 (this machine)
- Podman (for building container images)
- K3s (installed in step 2 below)
- Git access to the repo

No local Python, Node.js, or `.venv` needed - everything runs in containers.

## 2. Current Configuration

| Setting | Value |
|---------|-------|
| Port | **8080** (Traefik ingress, built into K3s) |
| S3 Storage | **MinIO** (K8s deployment) |
| Auth | Cookie-based signup/login (bcrypt passwords) |
| DNS | **CoreDNS** (built into K3s) |
| Ingress | **Traefik** (built into K3s) |
| This machine's LAN IP | `192.168.50.61` |
| Default gateway / router | `192.168.50.1` |

## 3. Install K3s

```bash
# Install K3s (single-node, includes Traefik + CoreDNS)
curl -sfL https://get.k3s.io | sh -

# Set up kubeconfig for non-root usage
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
export KUBECONFIG=~/.kube/config

# Verify K3s is running
sudo k3s kubectl get nodes
```

## 4. Open Port 8080 on This Machine (UFW Firewall)

```bash
# Check if ufw is active
sudo ufw status

# If ufw is inactive, enable it (this will also allow SSH so you don't lock yourself out)
sudo ufw allow OpenSSH
sudo ufw enable

# Allow port 8080 from your LAN subnet only (recommended)
sudo ufw allow from 192.168.50.0/24 to any port 8080 proto tcp

# OR allow port 8080 from anywhere (needed if you want internet access)
# sudo ufw allow 8080/tcp

# Verify the rule was added
sudo ufw status numbered
```

## 5. Architecture

```
     LAN :8080
          |
     [Traefik - built into K3s]
          |
    IngressRoute rules:
          |
   /login, /signup -----> [atlas-auth Service :5000]  (no middleware)
          |
   / , /api/*, /ws -----> strip-header middleware
                              |
                           forwardAuth middleware -> [atlas-auth :5000/auth]
                              |                      (copies X-User-Email)
                           [atlas-ui Service :8000]
          |
   (internal only) ------> [minio Service :9000]
```

Traefik replaces the old nginx container. Its `forwardAuth` middleware is equivalent to nginx `auth_request`. A `headers` middleware strips client-provided `X-User-Email` before forwardAuth sets the trusted value. CoreDNS provides reliable service discovery (no more podman DNS issues).

## 6. Deploy

```bash
cd /home/garlan/prod-atlas/atlas-ui-3/deploy/k3s

# Build all container images and import into k3s
./run.sh build

# Deploy to k3s
./run.sh up

# Check status
./run.sh status
```

Access ATLAS at `http://localhost:8080`. You'll be redirected to `/login`. Click "Create an account" to sign up, then log in.

## 7. Management Commands

```bash
cd /home/garlan/prod-atlas/atlas-ui-3/deploy/k3s

./run.sh build                   # Build images and import into k3s
./run.sh up                      # Deploy all manifests
./run.sh down                    # Delete atlas namespace (removes everything)
./run.sh restart                 # Restart all deployments
./run.sh restart atlas-ui        # Restart just the UI
./run.sh restart auth            # Restart just the auth service
./run.sh logs atlas-auth         # Follow auth service logs
./run.sh logs ui                 # Follow UI logs
./run.sh status                  # Show cluster and namespace status
```

## 8. Access from Other Devices on Your Network

Once ATLAS is running and the firewall is open:

| Device | URL |
|--------|-----|
| This machine | `http://localhost:8080` |
| Other LAN devices | `http://192.168.50.61:8080` |

## 9. Router Configuration (Port Forwarding)

If you want devices **outside** your local network (i.e., from the internet) to access ATLAS, set up port forwarding on your router.

1. **Open your router admin panel** - go to `http://192.168.50.1` in a browser
2. **Find Port Forwarding** - usually under "Advanced" > "Port Forwarding" or "NAT/Gaming"
3. **Create a new rule:**

   | Field | Value |
   |-------|-------|
   | Service Name | `ATLAS` |
   | Protocol | `TCP` |
   | External Port | `8080` (or `80`) |
   | Internal IP | `192.168.50.61` |
   | Internal Port | `8080` |

4. **Save and apply**

Find your public IP: `curl -s ifconfig.me`

## 10. Static IP (Recommended)

Your LAN IP may change if assigned via DHCP.

### Option A: Reserve the IP on your router (easiest)

1. Go to router admin (`http://192.168.50.1`)
2. Find "DHCP Reservation" (usually under LAN/DHCP settings)
3. Add a reservation for this machine's MAC address to `192.168.50.61`

### Option B: Set a static IP on this machine

```bash
sudo nano /etc/netplan/01-network-manager-all.yaml
```

```yaml
network:
  version: 2
  ethernets:
    eno1:  # check with: ip link show
      dhcp4: no
      addresses:
        - 192.168.50.61/24
      routes:
        - to: default
          via: 192.168.50.1
      nameservers:
        addresses:
          - 8.8.8.8
          - 8.8.4.4
```

```bash
sudo netplan apply
```

## 11. Start on Boot (systemd)

K3s itself starts on boot automatically. To auto-deploy the ATLAS namespace on boot:

```bash
sudo tee /etc/systemd/system/atlas.service << 'EOF'
[Unit]
Description=ATLAS UI K3s Deployment
After=k3s.service
Requires=k3s.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
WorkingDirectory=/home/garlan/prod-atlas/atlas-ui-3/deploy/k3s
ExecStart=/home/garlan/prod-atlas/atlas-ui-3/deploy/k3s/run.sh up
ExecStop=/home/garlan/prod-atlas/atlas-ui-3/deploy/k3s/run.sh down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable atlas
sudo systemctl start atlas

# Check status
sudo systemctl status atlas
```

## 12. User Accounts

User accounts are stored in `data/auth/users.json` (bcrypt-hashed passwords). This file persists across pod restarts via a hostPath volume mount.

To see registered users:

```bash
cat /home/garlan/prod-atlas/atlas-ui-3/data/auth/users.json | python3 -m json.tool
```

## 13. Troubleshooting

### Can't access from other devices

```bash
# 1. Verify K3s is running
sudo k3s kubectl get nodes

# 2. Verify pods are running
cd /home/garlan/prod-atlas/atlas-ui-3/deploy/k3s && ./run.sh status

# 3. Verify firewall is open
sudo ufw status | grep 8080

# 4. Test from this machine
curl -s http://localhost:8080 | head -5

# 5. Test from another machine on the LAN
curl -s http://192.168.50.61:8080 | head -5
```

### Pod won't start

```bash
# Check pod events for errors
sudo k3s kubectl describe pod -n atlas -l app=atlas-ui

# Check if images are imported
sudo k3s ctr images list | grep atlas

# Rebuild and reimport images
./run.sh build
./run.sh restart ui
```

### Image pull errors (ErrImageNeverPull)

This means the image hasn't been imported into k3s containerd. Rebuild:

```bash
./run.sh build
```

### Check logs

```bash
cd /home/garlan/prod-atlas/atlas-ui-3/deploy/k3s
./run.sh logs atlas-auth         # Auth service
./run.sh logs ui                 # ATLAS UI
./run.sh logs minio              # MinIO storage

# Or directly with kubectl
sudo k3s kubectl logs -f deployment/atlas-ui -n atlas
```

### Traefik issues

```bash
# Check Traefik logs
sudo k3s kubectl logs -f -n kube-system -l app.kubernetes.io/name=traefik

# Verify IngressRoutes
sudo k3s kubectl get ingressroute -n atlas

# Verify middlewares
sudo k3s kubectl get middleware -n atlas
```

### Reset everything

```bash
# Delete the namespace and redeploy
./run.sh down
./run.sh up
```

## 14. Podman-Compose (Legacy Alternative)

The old podman-compose deployment files are still available at `deploy/docker-compose.prod.yml`, `deploy/nginx.conf`, and `deploy/run.sh` if you prefer compose over K3s.

## Quick Reference

| What | Command / URL |
|------|---------------|
| Start stack | `cd deploy/k3s && ./run.sh up` |
| Stop stack | `cd deploy/k3s && ./run.sh down` |
| Rebuild + restart | `cd deploy/k3s && ./run.sh build && ./run.sh up` |
| Update from git | `git pull && cd deploy/k3s && ./run.sh build && ./run.sh up` |
| View logs | `cd deploy/k3s && ./run.sh logs` |
| Pod status | `cd deploy/k3s && ./run.sh status` |
| Local access | `http://localhost:8080` |
| LAN access | `http://192.168.50.61:8080` |
| Router admin | `http://192.168.50.1` |
| User accounts | `cat data/auth/users.json` |
| Firewall status | `sudo ufw status` |
| K3s node status | `sudo k3s kubectl get nodes` |
