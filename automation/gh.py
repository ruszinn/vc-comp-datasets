"""GitHub store — this automation's own GitHub Contents API client.

Purpose-built and self-contained (no shared client with any other service). Does
exactly what the pipeline needs: list the data dir, read a dataset, commit a
dataset, and build the raw URLs the Airtable rows use. One `requests.Session`
so the token is set once and reused — GitHub is hit the minimum number of times.

The token is the only high-value secret the pipeline holds: a fine-grained PAT
scoped to this one repo, Contents read+write.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Optional

import requests

_API = "https://api.github.com"


class GitHubStore:
    def __init__(self) -> None:
        self.repo = os.environ["GITHUB_REPO"]                       # "owner/name"
        self.branch = os.environ.get("GITHUB_BRANCH", "main")
        self.data_dir = os.environ.get("GITHUB_DATA_DIR", "data")
        self._s = requests.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "vc-comps-pipeline/1.0",
        })

    # --- reads ---------------------------------------------------------------
    def list_data_files(self) -> list[str]:
        r = self._s.get(f"{_API}/repos/{self.repo}/contents/{self.data_dir}",
                        params={"ref": self.branch}, timeout=30)
        r.raise_for_status()
        return [i["name"] for i in r.json() if i.get("type") == "file"]

    def read_json(self, filename: str) -> Optional[list]:
        """Return the dataset as a list, or None if the file doesn't exist yet
        (a brand-new firm — the pipeline treats None as 'no previous companies')."""
        r = self._s.get(
            f"{_API}/repos/{self.repo}/contents/{self.data_dir}/{filename}",
            headers={"Accept": "application/vnd.github.raw"},
            params={"ref": self.branch}, timeout=30,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        try:
            data = json.loads(r.text)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _blob_sha(self, path: str) -> Optional[str]:
        r = self._s.get(f"{_API}/repos/{self.repo}/contents/{path}",
                        params={"ref": self.branch}, timeout=30)
        return r.json().get("sha") if r.status_code == 200 else None

    # --- write ---------------------------------------------------------------
    def commit_json(self, filename: str, records: list, message: str) -> str:
        """Create or overwrite data/<filename> with `records`. Returns commit sha."""
        path = f"{self.data_dir}/{filename}"
        content = json.dumps(records, indent=2, ensure_ascii=False) + "\n"
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        sha = self._blob_sha(path)
        if sha:
            payload["sha"] = sha
        r = self._s.put(f"{_API}/repos/{self.repo}/contents/{path}",
                        json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    def delete_json(self, filename: str, message: str) -> bool:
        """Delete data/<filename> from the repo (git history keeps it, so this
        is always recoverable). Used by bespoke-or-nothing retirement: a firm
        whose factory burst fails loses its thin dataset rather than freezing
        it. Returns False if the file wasn't there — a re-run is a no-op."""
        path = f"{self.data_dir}/{filename}"
        sha = self._blob_sha(path)
        if not sha:
            return False
        r = self._s.delete(f"{_API}/repos/{self.repo}/contents/{path}",
                           json={"message": message, "sha": sha,
                                 "branch": self.branch}, timeout=30)
        r.raise_for_status()
        return True

    # --- urls ----------------------------------------------------------------
    def raw_url(self, filename: str) -> str:
        return (f"https://raw.githubusercontent.com/{self.repo}/"
                f"{self.branch}/{self.data_dir}/{filename}")
