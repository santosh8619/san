
#!/usr/bin/env python3
"""
Get Kubernetes versions for org IDs from Horizon Edge Deployments API and append to CSV.

Usage example:
    python get_k8s_version_from_orgs.py \
        --token "eyJhbGciOi..." \
        --csv ./orgs.csv \
        --output ./orgs_with_versions.csv \
        --org-column "org_id" \
        --page-size 50 \
        --verbose

Notes:
- The API is called: https://cloud-sg.horizon.omnissa.com/admin/v2/edge-deployments
- Query params: page, size, org_id, include_reported_status=true
- We safely parse nested JSON and collect all versions from CLUSTER deployments.
"""

import argparse
import csv
import json
import logging
import time
from typing import Dict, Any, List, Set, Optional, Tuple

import requests


BASE_URL = "https://cloud-sg.horizon.omnissa.com/admin/v2/edge-deployments"


def setup_logger(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append Kubernetes version(s) from Horizon Edge Deployments to a CSV, per org_id."
    )
    parser.add_argument("--token", required=True, help="Access token (Bearer).")
    parser.add_argument("--csv", required=True, help="Path to input CSV file.")
    parser.add_argument("--output", default=None,
                        help="Path to output CSV file (default: overwrite input by writing to <input>_out.csv).")
    parser.add_argument("--org-column", default="org_id",
                        help="CSV column name containing org IDs (default: org_id). Fallback: 'org id'.")
    parser.add_argument("--page-size", type=int, default=20,
                        help="Items per page for API pagination (default: 20).")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="HTTP timeout in seconds (default: 20).")
    parser.add_argument("--retries", type=int, default=3,
                        help="Number of retries on transient errors (default: 3).")
    parser.add_argument("--backoff", type=float, default=1.5,
                        help="Exponential backoff factor (default: 1.5).")
    parser.add_argument("--sleep", type=float, default=0.2,
                        help="Sleep between requests to avoid rate limits (default: 0.2s).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    return parser.parse_args()


def headers(token: str) -> Dict[str, str]:
    # Do NOT log token anywhere
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text, "status_code": resp.status_code}


def call_api(url: str, params: Dict[str, Any], hdrs: Dict[str, str],
             timeout: float, retries: int, backoff: float) -> Tuple[int, Any, str]:
    """GET with retries. Returns (status_code, json_or_dict, error_message)."""
    attempt = 0
    last_err = ""
    while attempt <= retries:
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            status = resp.status_code
            data = safe_json(resp)

            # Retry on 429 or 5xx
            if status == 429 or (500 <= status < 600):
                last_err = f"Transient error {status}: {str(data)[:300]}"
                attempt += 1
                if attempt <= retries:
                    sleep_s = backoff ** attempt
                    logging.warning(f"{last_err}. Retrying in {sleep_s:.1f}s (attempt {attempt}/{retries})...")
                    time.sleep(sleep_s)
                continue

            return status, data, ""
        except requests.RequestException as e:
            last_err = str(e)
            attempt += 1
            if attempt <= retries:
                sleep_s = backoff ** attempt
                logging.warning(f"Network error: {last_err}. Retrying in {sleep_s:.1f}s (attempt {attempt}/{retries})...")
                time.sleep(sleep_s)

    return 0, {}, last_err or "Max retries exceeded"


def collect_k8s_versions_from_json(data: Any) -> List[str]:
    """
    Walk arbitrary JSON and collect kubernetesVersion values for CLUSTER deployments.

    We look for objects like:
      {
        "deploymentModeDetails": {
          "type": "CLUSTER",
          "attributes": {
             "kubernetesVersion": "...",
             "desiredKubernetesVersion": "..."  # fallback
          }
        }
      }
    """
    versions: Set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            dmd = obj.get("deploymentModeDetails")
            if isinstance(dmd, dict):
                if dmd.get("type") == "CLUSTER":
                    attrs = dmd.get("attributes", {}) or {}
                    v = attrs.get("kubernetesVersion") or attrs.get("desiredKubernetesVersion")
                    if v:
                        versions.add(str(v))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return sorted(versions)


def get_versions_for_org(org_id: str, token: str, page_size: int,
                         timeout: float, retries: int, backoff: float, sleep_s: float) -> Tuple[List[str], str]:
    """
    Fetch all pages for an org_id and return versions list and an error string (empty if OK).
    Handles common paginated response shapes: plain list OR {"content": [...], "last": bool}.
    """
    hdrs = headers(token)
    page = 0
    versions: Set[str] = set()
    error_msg = ""

    while True:
        params = {
            "page": page,
            "size": page_size,
            "org_id": org_id,
            "include_reported_status": "true",
        }

        status, data, err = call_api(BASE_URL, params, hdrs, timeout, retries, backoff)
        if err:
            error_msg = f"HTTP error: {err}"
            break

        if status == 401:
            error_msg = "Unauthorized (401). Check token."
            break
        if status and not (200 <= status < 300):
            error_msg = f"Bad status {status}"
            break

        # Collect versions from this page
        page_versions = collect_k8s_versions_from_json(data)
        for v in page_versions:
            versions.add(v)

        # Decide if there are more pages
        has_more = False
        if isinstance(data, dict):
            # Spring-style page object: content + last flag
            content = data.get("content")
            last = data.get("last")
            if isinstance(content, list):
                if last is True:
                    has_more = False
                else:
                    # If not explicitly last, infer from size
                    has_more = len(content) == page_size
            else:
                # If content isn't a list, probably not paged
                has_more = False
        elif isinstance(data, list):
            # Plain list (likely not paged)
            has_more = False
        else:
            # Unknown shape; avoid infinite loop
            has_more = False

        if not has_more:
            break

        page += 1
        time.sleep(sleep_s)  # gentle rate-limiting

    return sorted(versions), error_msg


def main():
    args = parse_args()
    setup_logger(args.verbose)

    # Decide output path
    output_path = args.output or f"{args.csv.rsplit('.', 1)[0]}_out.csv"

    # Read input CSV
    with open(args.csv, newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        fieldnames = list(reader.fieldnames or [])

        # Try to locate org column
        org_col = args.org_column
        if org_col not in fieldnames:
            # Fallback to 'org id'
            if "org id" in fieldnames:
                org_col = "org id"
                logging.warning("Using fallback column name 'org id'.")
            else:
                raise ValueError(
                    f"Org column '{args.org_column}' not found. Available columns: {fieldnames}"
                )

        # Prepare writer with extra columns
        extra_cols = ["kubernetesVersion", "error"]
        with open(output_path, "w", newline="", encoding="utf-8") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames + extra_cols)
            writer.writeheader()

            rows = list(reader)
            total = len(rows)
            logging.info(f"Processing {total} rows from {args.csv}...")

            for i, row in enumerate(rows, start=1):
                org_id = (row.get(org_col) or "").strip()
                if not org_id:
                    row.update({"kubernetesVersion": "", "error": "Missing org_id"})
                    writer.writerow(row)
                    continue

                versions, err = get_versions_for_org(
                    org_id=org_id,
                    token=args.token,
                    page_size=args.page_size,
                    timeout=args.timeout,
                    retries=args.retries,
                    backoff=args.backoff,
                    sleep_s=args.sleep
                )

                if err:
                    logging.error(f"[{i}/{total}] Org {org_id}: {err}")
                else:
                    logging.info(f"[{i}/{total}] Org {org_id}: versions={versions}")

                # If multiple versions found, join with '|'
                row.update({
                    "kubernetesVersion": "|".join(versions) if versions else "",
                    "error": err
                })
                writer.writerow(row)

    logging.info(f"Done. Results written to: {output_path}")


if __name__ == "__main__":
    main()
