## Runbook: VDI Session Troubleshooting (Horizon Cloud on Azure)
**Owner:** VDI On-Call / Horizon Cloud Ops | **Frequency:** As needed (incident-triggered)
**Last Updated:** 2026-07-02 | **Last Run:** —

### Purpose
Diagnose and resolve VDI session failures in Horizon Cloud on Azure — black screens after login, login loops, mid-session drops, and "connection to remote computer failed/ended" errors — by walking the connection path from client → Horizon Edge/UAG → Connection Server/pod → target VM.

### Prerequisites
- [ ] Admin access to the Horizon Console
- [ ] Admin access to Horizon Edge / UAG (port 9443)
- [ ] `kubectl` access to the AKS cluster hosting Horizon Cloud pod/control-plane components
- [ ] Azure Portal/CLI access (via the ops service principal) to NSGs and VNets in the Horizon Cloud subscription
- [ ] Grafana access — Horizon Cloud and Azure infra dashboards
- [ ] Kibana access — Horizon Edge/UAG and VM agent logs
- [ ] Affected user's username, pool/farm name, and approximate failure time

### Procedure

#### Step 1: Confirm scope and symptom
```
Ask the user: black screen after login, indefinite login loop,
mid-session drop, or an explicit connection error?
Check whether it's one user, one pool, one pod/datacenter, or global.
```
**Expected result:** Clear symptom description plus blast radius.
**If it fails:** If more than a handful of users are affected, open a P1 bridge before continuing.

#### Step 2: Check Horizon Edge / UAG health
```
Horizon Console → Monitor → Edge/UAG status
```
**Expected result:** All Edge/UAG nodes show "Available"; TLS cert on both the admin (9443) and internal interfaces is valid and matches the UAG FQDN.
**If it fails:** Restart or re-register the unhealthy node; renew the cert if expired; fail over to a healthy UAG if the pool has more than one.

#### Step 3: Validate the connection path
A Horizon connection has two phases — XML-API over HTTPS (auth/session brokering), then the Blast/PCoIP display-protocol handoff. Test both from an affected client:
```
curl -vk https://<uag-fqdn>:443
curl -vk https://<uag-fqdn>:9443
```
**Expected result:** Valid cert chain returned, matching the UAG FQDN, on both ports.
**If it fails:** Cert mismatch or NSG/firewall block — check NSG rules for 443, 8443, 4172 (PCoIP), 22443 (Blast) and correct.

#### Step 4: Confirm gateway configuration
On the Connection Server / pod config, confirm **Blast Secure Gateway** and **PCoIP Secure Gateway** are disabled — the UAG should own both when it sits in front of the connection.
**Expected result:** Both gateways disabled on the Connection Server.
**If it fails:** Disable them and retest. Leaving these enabled behind a UAG is one of the most common causes of black/blank screens.

#### Step 5: Check the network path for interference
```
Confirm required Horizon ports (443, 8443, 4172, 22443) are open
end-to-end across NSGs and any on-prem firewall.
Check for passive/transparent HTTPS inspection or a proxy in the path.
```
**Expected result:** All required ports open; no TLS interception on Blast/PCoIP traffic.
**If it fails:** Add NSG/firewall exceptions for Horizon FQDNs and ports; exclude Horizon traffic from HTTPS-inspecting proxies.

#### Step 6: Check the target VM and agent
```
Horizon Console → Sessions (or console into the VM)
Check Horizon Agent service status
```
Cross-check Kibana for Horizon Agent/Blast log errors around the failure timestamp.
**Expected result:** Agent service running; no repeated crash or error entries.
**If it fails:** Restart the Horizon Agent service, or restart/recompose the VM (instant clones). Note: first-login-of-the-day black screens are a known issue on some Windows 11 golden images — recompose if this matches the pattern.

#### Step 7: Check AKS-hosted control-plane components
```
kubectl get pods -n <horizon-cloud-namespace>
kubectl logs <pod-name> -n <horizon-cloud-namespace> --since=1h
```
**Expected result:** All relevant pods `Running`/`Ready`, no `CrashLoopBackOff`.
**If it fails:** Restart the failing pod; check node-pool capacity/autoscaling; escalate if a control-plane component itself is degraded.

#### Step 8: Correlate with monitoring
```
Grafana → session failure rate, UAG CPU/connection count,
Azure network latency — around the incident window
```
**Expected result:** No infrastructure-wide anomaly. If isolated to one user, close out with the targeted fix above; if widespread, treat as an incident and escalate.

### Verification
- [ ] User logs in and reaches the desktop without black screen or loop
- [ ] Session holds for 15+ minutes without dropping
- [ ] Horizon Console shows the UAG/Edge node healthy post-fix

### Troubleshooting
| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Black screen after login | Blast/PCoIP Secure Gateway still enabled on Connection Server behind a UAG | Disable both gateways on the Connection Server |
| "Failed to resolve proxying route for request" | UAG can't reach the Connection Server/pod backend | Check UAG→backend connectivity, DNS resolution, NSG rules |
| "The connection to the remote computer failed/ended" | Cert mismatch, expired cert, or blocked port | Renew/rebind the cert; open the required ports |
| Recurring first-login-of-the-day black screen | Known image issue on some Windows 11 golden images | Recompose/reset the VM; check golden-image patch backlog |
| Intermittent Blast disconnects | Network latency/packet loss, or HTTPS inspection on the path | Exclude Horizon FQDNs from inspection; check Grafana for latency spikes |

### Rollback
If a config change (gateway toggle, cert rebind, NSG rule) doesn't resolve the issue or introduces a new one, revert to the last known-good UAG/Connection Server configuration or snapshot before escalating further.

### Escalation
| Situation | Contact | Method |
|-----------|---------|--------|
| NSG/VNet change needed | Azure networking team | Slack / on-call page |
| UAG/Horizon Edge appliance failure | Horizon Cloud ops on-call | PagerDuty / Slack |
| AKS control-plane degraded | Platform/AKS on-call | PagerDuty |
| Pool-wide or multi-user outage (P1) | Incident commander | Bridge call |

### History
| Date | Run By | Notes |
|------|--------|-------|
| | | |

### Related resources
- Internal: [Titan – Ops Runbooks (Confluence)](https://omnissa.atlassian.net/wiki/spaces/DevOps/pages/47012772/V2+-+Titan+-+Ops+Runbooks)
- Omnissa Tech Zone: [Understand and Troubleshoot Horizon Connections](https://techzone.omnissa.com/resource/understand-and-troubleshoot-horizon-connections)
- Omnissa Tech Zone: [Horizon Cloud configuration overview](https://techzone.omnissa.com/resource/horizon-cloud-configuration)
- Omnissa KB: [Troubleshooting UAG issues (6001197)](https://kb.omnissa.com/s/article/6001197?lang=en_US)
- Omnissa Community: [UAG common errors and configuration issues](https://community.omnissa.com/forums/topic/68442-unified-access-gateway-uag-common-errors-and-configurations-issues/)
