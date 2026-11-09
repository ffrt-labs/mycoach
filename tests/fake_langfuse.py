"""An in-memory Langfuse prompts and datasets API, served to a real client over httpx.

Implements the endpoints MyCoach uses: prompts (list by name, get by label,
create) and datasets (get by name, list its items). ``down = True`` makes
every request fail as a connection error.
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx
from langfuse import Langfuse
from langfuse._client.resource_manager import LangfuseResourceManager

_PREFIX = "/api/public/v2/prompts"
_DATASETS = "/api/public/v2/datasets/"
_DATASET_ITEMS = "/api/public/dataset-items"
_STAMP = "2026-11-08T00:00:00Z"


@dataclass
class FakeLangfusePrompts:
    versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    datasets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False

    def add_item(self, dataset: str, input: Any, *, status: str = "ACTIVE") -> str:
        """Store a dataset item (creating the dataset); returns its id."""
        items = self.datasets.setdefault(dataset, [])
        item_id = f"item-{len(items) + 1}"
        items.append(
            {
                "id": item_id,
                "status": status,
                "input": input,
                "expectedOutput": None,
                "metadata": None,
                "datasetId": f"ds-{dataset}",
                "datasetName": dataset,
                "createdAt": _STAMP,
                "updatedAt": _STAMP,
                "mediaReferences": [],
            }
        )
        return item_id

    def add(
        self, name: str, messages: list[dict[str, str]], labels: list[str] | None = None
    ) -> int:
        """Store a new chat version; returns its number. A label moves to it."""
        labels = ["production"] if labels is None else labels
        history = self.versions.setdefault(name, [])
        for old in history:
            old["labels"] = [label for label in old["labels"] if label not in labels]
        version = len(history) + 1
        history.append(
            {
                "name": name,
                "version": version,
                "type": "chat",
                "prompt": [{"type": "chatmessage", **m} for m in messages],
                "config": {},
                "labels": labels,
                "tags": [],
            }
        )
        return version

    def fetches(self) -> int:
        """How many single-prompt GETs were made (the serving path)."""
        return sum(
            1 for r in self.requests if r.method == "GET" and r.url.path.startswith(_PREFIX + "/")
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("Langfuse is down", request=request)
        raw_path = request.url.raw_path.decode().split("?", 1)[0]
        if raw_path == _PREFIX and request.method == "GET":
            return self._list(request.url.params.get("name", ""))
        if raw_path == _PREFIX and request.method == "POST":
            body = json.loads(request.content)
            version = self.add(body["name"], body["prompt"], body.get("labels", []))
            return httpx.Response(200, json=self.versions[body["name"]][version - 1])
        if raw_path.startswith(_PREFIX + "/") and request.method == "GET":
            name = unquote(raw_path[len(_PREFIX) + 1 :])
            return self._get(name, request.url.params.get("label", "production"))
        if raw_path.startswith(_DATASETS) and request.method == "GET":
            return self._dataset(unquote(raw_path[len(_DATASETS) :]))
        if raw_path == _DATASET_ITEMS and request.method == "GET":
            return self._items(unquote(request.url.params.get("datasetName", "")))
        return httpx.Response(404, json={"message": "no such route"})

    def _dataset(self, name: str) -> httpx.Response:
        if name not in self.datasets:
            return httpx.Response(404, json={"message": "Dataset not found"})
        return httpx.Response(
            200,
            json={
                "id": f"ds-{name}",
                "name": name,
                "metadata": None,
                "projectId": "project-1",
                "createdAt": _STAMP,
                "updatedAt": _STAMP,
            },
        )

    def _items(self, name: str) -> httpx.Response:
        items = self.datasets.get(name, [])
        meta = {"page": 1, "limit": 50, "totalItems": len(items), "totalPages": 1}
        return httpx.Response(200, json={"data": items, "meta": meta})

    def _list(self, name: str) -> httpx.Response:
        history = self.versions.get(name, [])
        data = []
        if history:
            data.append(
                {
                    "name": name,
                    "type": "chat",
                    "versions": [v["version"] for v in history],
                    "labels": sorted({label for v in history for label in v["labels"]}),
                    "tags": [],
                    "lastUpdatedAt": "2026-11-08T00:00:00Z",
                    "lastConfig": {},
                }
            )
        meta = {"page": 1, "limit": 50, "totalItems": len(data), "totalPages": 1}
        return httpx.Response(200, json={"data": data, "meta": meta})

    def _get(self, name: str, label: str) -> httpx.Response:
        for version in self.versions.get(name, []):
            if label in version["labels"]:
                return httpx.Response(200, json=version)
        return httpx.Response(404, json={"message": "Prompt not found"})


@contextmanager
def langfuse_over(fake: FakeLangfusePrompts) -> Generator[Langfuse, None, None]:
    """A real Langfuse client whose API calls go to ``fake``; tracing off."""
    LangfuseResourceManager.reset()
    client = Langfuse(
        public_key="pk-lf-prompts",
        secret_key="sk-lf-prompts",
        base_url="http://langfuse.invalid",
        httpx_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        tracing_enabled=False,
    )
    try:
        yield client
    finally:
        LangfuseResourceManager.reset()
