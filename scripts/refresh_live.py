#!/usr/bin/env python3
"""Collect live signals for the akanjilal.dev hero box and write assets/data/live.json.

Two halves:
  - mcp: Model Context Protocol servers published by AWS, Google, and Microsoft (public GitHub, no keys).
  - quantum: device availability and health for Amazon Braket, IBM Quantum, Microsoft Azure
    Quantum, and Google Quantum AI. Device level data needs vendor credentials, supplied to the
    GitHub Actions job as secrets. When a credential is missing the provider degrades to
    token_required and the page shows a "connect a key" hint rather than guessing.

Fault tolerant: one provider failing never aborts the run. Failures land in the errors list.
Python 3.11+. Uses requests for GitHub. boto3, qiskit-ibm-runtime, and azure-quantum are optional
and only used when their credentials are present.
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

UA = "akanjilal-live-data"
GH = "https://api.github.com"
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
ERRORS = []


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def gh_json(path):
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return get_json(f"{GH}{path}", headers=headers)


# ---------------- MCP half ----------------
def mcp_aws():
    items = gh_json("/repos/awslabs/mcp/contents/src")
    names = sorted(it["name"] for it in items if it.get("type") == "dir")
    return {
        "provider": "AWS",
        "repo": "awslabs/mcp",
        "url": "https://github.com/awslabs/mcp",
        "server_count": len(names),
        "servers": names[:8],
        "status": "operational",
    }


def mcp_google():
    repos = ["googleapis/gcloud-mcp", "googleapis/genai-toolbox"]
    present = []
    for r in repos:
        try:
            gh_json(f"/repos/{r}")
            present.append(r.split("/")[1])
        except Exception:
            pass
    return {
        "provider": "Google",
        "repo": "googleapis/gcloud-mcp",
        "url": "https://github.com/googleapis/gcloud-mcp",
        "server_count": len(present) if present else None,
        "servers": present,
        "status": "operational",
    }


def mcp_microsoft():
    gh_json("/repos/Azure/azure-mcp")  # confirm it resolves
    return {
        "provider": "Microsoft",
        "repo": "Azure/azure-mcp",
        "url": "https://github.com/Azure/azure-mcp",
        "server_count": 1,
        "servers": ["azure-mcp"],
        "status": "operational",
        "note": "One server that fans out into many Azure service namespaces.",
    }


def collect_mcp():
    providers = []
    for label, fn in (("aws", mcp_aws), ("google", mcp_google), ("microsoft", mcp_microsoft)):
        try:
            providers.append(fn())
        except Exception as e:
            ERRORS.append(f"mcp {label}: {e}")
    return {"providers": providers}


# ---------------- Quantum half ----------------
def token_required(provider, url, secret_hint):
    return {
        "provider": provider, "status": "token_required", "source": "token_required",
        "device_count": None, "online_count": None, "devices": [], "url": url,
        "note": f"Add {secret_hint} as a repository secret to show live devices.",
    }


def quantum_braket():
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        return token_required("Amazon Braket", "https://aws.amazon.com/braket/", "the AWS_* secrets")
    import boto3  # only when credentials exist
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("braket", region_name=region)
    resp = client.search_devices(filters=[])
    devices = []
    for d in resp.get("devices", []):
        # only real, current quantum processors: drop simulators and retired hardware
        if d.get("deviceType") != "QPU" or d.get("deviceStatus") == "RETIRED":
            continue
        online = d.get("deviceStatus") == "ONLINE"
        qubits = None
        try:
            cap = json.loads(client.get_device(deviceArn=d["deviceArn"]).get("deviceCapabilities", "{}"))
            qubits = cap.get("paradigm", {}).get("qubitCount")
        except Exception:
            pass
        devices.append({
            "name": ((d.get("providerName") or "") + " " + (d.get("deviceName") or "")).strip(),
            "provider": d.get("providerName"),
            "qubits": qubits, "status": "online" if online else "offline",
        })
    devices.sort(key=lambda d: (d["status"] != "online", d["name"]))
    online_count = sum(1 for d in devices if d["status"] == "online")
    status = "operational" if online_count else ("degraded" if devices else "down")
    note = ", ".join(d["name"] + (" " + str(d["qubits"]) + "q" if d["qubits"] else "") + " (" + d["status"] + ")"
                     for d in devices) or None
    return {
        "provider": "Amazon Braket", "status": status, "source": "api",
        "device_count": len(devices), "online_count": online_count, "devices": devices,
        "url": "https://%s.console.aws.amazon.com/braket/home?region=%s#/devices" % (region, region),
        "region": region, "note": note,
    }


def quantum_ibm():
    token = os.environ.get("IBM_QUANTUM_TOKEN")
    if not token:
        return token_required("IBM Quantum", "https://quantum.ibm.com", "IBM_QUANTUM_TOKEN")
    from qiskit_ibm_runtime import QiskitRuntimeService
    instance = os.environ.get("IBM_QUANTUM_INSTANCE")
    service = None
    for channel in ("ibm_quantum_platform", "ibm_cloud", "ibm_quantum"):
        try:
            service = QiskitRuntimeService(channel=channel, token=token, instance=instance)
            break
        except Exception:
            continue
    if service is None:
        raise RuntimeError("could not open a QiskitRuntimeService channel")
    devices = []
    for b in service.backends():
        try:
            st = b.status()
            devices.append({
                "name": b.name, "provider": "IBM", "qubits": getattr(b, "num_qubits", None),
                "status": "online" if getattr(st, "operational", False) else "offline",
                "queue": getattr(st, "pending_jobs", None),
            })
        except Exception:
            continue
    online_count = sum(1 for d in devices if d["status"] == "online")
    status = "operational" if online_count else ("degraded" if devices else "down")
    return {
        "provider": "IBM Quantum", "status": status, "source": "api",
        "device_count": len(devices), "online_count": online_count, "devices": devices,
        "url": "https://quantum.ibm.com",
    }


def quantum_azure():
    need = ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID",
            "AZURE_RESOURCE_GROUP", "AZURE_QUANTUM_WORKSPACE", "AZURE_QUANTUM_LOCATION"]
    if not all(os.environ.get(k) for k in need):
        return {
            "provider": "Microsoft Azure Quantum", "status": "public", "source": "public",
            "device_count": None, "online_count": None, "devices": [],
            "url": "https://azure.microsoft.com/products/quantum",
            "note": "Public platform status only. Device availability would need an Azure Quantum workspace and credentials.",
        }
    from azure.quantum import Workspace
    from azure.identity import ClientSecretCredential
    cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], os.environ["AZURE_CLIENT_ID"],
                                  os.environ["AZURE_CLIENT_SECRET"])
    ws = Workspace(subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
                   resource_group=os.environ["AZURE_RESOURCE_GROUP"],
                   name=os.environ["AZURE_QUANTUM_WORKSPACE"],
                   location=os.environ["AZURE_QUANTUM_LOCATION"], credential=cred)
    devices = []
    for t in ws.get_targets():
        avail = str(getattr(t, "current_availability", "")).lower()
        devices.append({
            "name": getattr(t, "name", str(t)), "provider": "Azure", "qubits": None,
            "status": "online" if avail in ("available", "") else "offline",
        })
    online_count = sum(1 for d in devices if d["status"] == "online")
    status = "operational" if online_count else ("degraded" if devices else "down")
    return {
        "provider": "Microsoft Azure Quantum", "status": status, "source": "api",
        "device_count": len(devices), "online_count": online_count, "devices": devices,
        "url": "https://azure.microsoft.com/products/quantum",
    }


def quantum_google():
    # Google Quantum AI has no public device availability API. Show the platform honestly.
    return {
        "provider": "Google Quantum AI", "status": "public", "source": "public",
        "device_count": None, "online_count": None, "devices": [],
        "url": "https://quantumai.google/",
        "note": "No public device availability API. Access is through Google Cloud and is invitation based.",
    }


def collect_quantum():
    providers = []
    for label, fn in (("braket", quantum_braket), ("ibm", quantum_ibm),
                      ("azure", quantum_azure), ("google", quantum_google)):
        try:
            providers.append(fn())
        except Exception as e:
            ERRORS.append(f"quantum {label}: {e}")
            url = {"braket": "https://aws.amazon.com/braket/", "ibm": "https://quantum.ibm.com",
                   "azure": "https://azure.microsoft.com/products/quantum", "google": "https://quantumai.google/"}[label]
            name = {"braket": "Amazon Braket", "ibm": "IBM Quantum",
                    "azure": "Microsoft Azure Quantum", "google": "Google Quantum AI"}[label]
            providers.append({"provider": name, "status": "unknown", "source": "error",
                              "device_count": None, "online_count": None, "devices": [], "url": url,
                              "note": "Could not read this provider on the last run."})
    return {"providers": providers}


def main():
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    out = {
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "next_refresh_at": (generated + timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
        "refresh_cadence_hours": 4,
        "mcp": collect_mcp(),
        "quantum": collect_quantum(),
        "errors": ERRORS,
    }
    os.makedirs("assets/data", exist_ok=True)
    with open("assets/data/live.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"mcp {len(out['mcp']['providers'])} providers, "
          f"quantum {len(out['quantum']['providers'])} providers, errors {len(ERRORS)}")


if __name__ == "__main__":
    main()
