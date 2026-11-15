from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import subprocess
import time
from urllib.parse import urljoin, urlsplit

import requests

BASE = "https://api.fabric.microsoft.com/v1/"
RESOURCE = "https://api.fabric.microsoft.com"


class DeploymentError(RuntimeError):
    pass


def azure(*args):
    try:
        result = subprocess.run(
            ["az", *args], check=True, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise DeploymentError("Azure CLI is required. Rebuild the dev container.") from exc
    except subprocess.CalledProcessError as exc:
        raise DeploymentError(f"Azure CLI failed: {exc.stderr.strip()}") from exc
    return result.stdout


def login(tenant):
    # Device-code instructions must remain visible in a remote Codespace.
    try:
        subprocess.run(
            ["az", "login", "--tenant", tenant, "--use-device-code",
             "--allow-no-subscriptions", "--output", "none"],
            check=True,
        )
    except FileNotFoundError as exc:
        raise DeploymentError("Azure CLI is required. Rebuild the dev container.") from exc
    except subprocess.CalledProcessError as exc:
        raise DeploymentError("Microsoft sign-in failed; no deployment was attempted.") from exc


class Fabric:
    def __init__(self, tenant, timeout=1800):
        self.tenant = tenant
        self.timeout = timeout
        self.session = requests.Session()
        self._token = None
        self._refresh_at = 0

    def url(self, path):
        url = urljoin(BASE, path)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "api.fabric.microsoft.com"
            or not parts.path.startswith("/v1/")
        ):
            raise DeploymentError("Refusing to send a Fabric token to an unexpected URL.")
        return url

    def token(self):
        if time.monotonic() >= self._refresh_at:
            data = json.loads(azure(
                "account", "get-access-token", "--tenant", self.tenant,
                "--resource", RESOURCE, "--output", "json",
            ))
            self._token = data["accessToken"]
            self._refresh_at = time.monotonic() + 240
        return self._token

    def request(self, method, path, body=None):
        url = self.url(path)
        deadline = time.monotonic() + 120
        for attempt in range(4):
            try:
                response = self.session.request(
                    method, url, json=body,
                    headers={"Authorization": f"Bearer {self.token()}"},
                    timeout=60, allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise DeploymentError(
                    f"{method} {urlsplit(url).path}: network failure. "
                    "A write may have completed; inspect Fabric before retrying."
                ) from exc
            if method == "GET" and response.status_code in (429, 502, 503, 504) and attempt < 3:
                self.pause(response, delay=2 ** attempt, deadline=deadline)
                continue
            if not 200 <= response.status_code < 300:
                request_id = response.headers.get("requestId", "unavailable")
                raise DeploymentError(
                    f"{method} {urlsplit(url).path}: HTTP {response.status_code}; "
                    f"request ID {request_id}. {response.text[:1000]}"
                )
            return response
        raise AssertionError("Unreachable")

    def pause(self, response, delay=5, deadline=None):
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                retry_after = float(header)
            except ValueError:
                try:
                    retry_after = (
                        parsedate_to_datetime(header) - datetime.now(timezone.utc)
                    ).total_seconds()
                except (TypeError, ValueError) as exc:
                    raise DeploymentError(f"Invalid Retry-After value: {header}") from exc
            if not math.isfinite(retry_after):
                raise DeploymentError(f"Invalid Retry-After value: {header}")
            delay = max(delay, retry_after)
        if deadline is not None and time.monotonic() + delay >= deadline:
            raise DeploymentError("Fabric operation timed out. Inspect its status before retrying.")
        time.sleep(delay)

    def list(self, path):
        values = []
        seen = set()
        while path:
            url = self.url(path)
            if url in seen:
                raise DeploymentError("Fabric returned a repeated continuation URL.")
            seen.add(url)
            data = self.request("GET", url).json()
            values.extend(data["value"])
            path = data.get("continuationUri")
        return values

    def create(self, path, body):
        response = self.request("POST", path, body)
        if response.status_code != 202:
            return response.json()
        location = response.headers.get("Location")
        if not location:
            raise DeploymentError("Fabric accepted the operation without a polling URL.")
        deadline = time.monotonic() + self.timeout
        while True:
            self.pause(response, deadline=deadline)
            response = self.request("GET", location)
            status = response.json()["status"]
            if status == "Succeeded":
                return self.request("GET", location.rstrip("/") + "/result").json()
            if status in ("Failed", "Cancelled"):
                raise DeploymentError(f"Fabric operation {status}: {response.text[:1000]}")

    def update_definition(self, workspace, item, definition):
        response = self.request(
            "POST", f"workspaces/{workspace}/items/{item}/updateDefinition",
            {"definition": definition},
        )
        if response.status_code != 202:
            return
        location = response.headers.get("Location")
        if not location:
            raise DeploymentError("Definition update has no polling URL. Inspect Fabric before retrying.")
        deadline = time.monotonic() + self.timeout
        while True:
            self.pause(response, deadline=deadline)
            response = self.request("GET", location)
            status = response.json()["status"]
            if status == "Succeeded":
                return
            if status in ("Failed", "Cancelled"):
                raise DeploymentError(f"Definition update {status}: {response.text[:1000]}")

    def delete(self, path):
        response = self.request("DELETE", path)
        if response.status_code == 202:
            location = response.headers.get("Location")
            if not location:
                raise DeploymentError("Deletion has no polling URL. Inspect Fabric before retrying.")
            deadline = time.monotonic() + self.timeout
            while True:
                self.pause(response, deadline=deadline)
                response = self.request("GET", location)
                status = response.json()["status"]
                if status == "Succeeded":
                    break
                if status in ("Failed", "Cancelled"):
                    raise DeploymentError(f"Deletion {status}: {response.text[:1000]}")

    def run(self, workspace, item, job_type, on_started):
        response = self.request(
            "POST", f"workspaces/{workspace}/items/{item}/jobs/instances?jobType={job_type}"
        )
        location = response.headers.get("Location")
        if not location:
            raise DeploymentError("Fabric returned no job polling URL.")
        self.url(location)
        on_started(location)
        self.wait_job(location, initial_response=response)

    def wait_job(self, location, initial_response=None):
        deadline = time.monotonic() + self.timeout
        if initial_response is not None:
            self.pause(initial_response, deadline=deadline)
        while time.monotonic() < deadline:
            response = self.request("GET", location)
            status = response.json()["status"]
            if status == "Completed":
                return
            if status in ("Failed", "Cancelled", "Deduped"):
                raise DeploymentError(f"Fabric job {status}: {response.text[:1000]}")
            self.pause(response, deadline=deadline)
        raise DeploymentError("Fabric job timed out; its URL is saved in deployment state.")
