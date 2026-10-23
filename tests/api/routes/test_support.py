"""Public support contracts use a mocked GitHub connection and no database."""

import io
import json
from copy import deepcopy
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from papyrus.config import Settings, get_settings
from papyrus.main import create_app
from papyrus.services import support

PAYLOAD = {
    "kind": "bug",
    "title": "Book does not open",
    "description": "Opening an EPUB leaves an empty reader.",
    "app_version": "0.0.4 (4)",
    "platform": "android",
}

PROJECT = {
    "data": {
        "organization": {
            "projectV2": {
                "id": "project-id",
                "viewerCanUpdate": True,
                "status": {"id": "status-field", "options": [{"id": "backlog-option", "name": "Backlog"}]},
                "kind": {"id": "kind-field", "options": [{"id": "task-option", "name": "Task"}]},
            }
        }
    }
}

ISSUE = {"number": 42, "node_id": "issue-id"}
PROJECT_ITEM = {"data": {"addProjectV2ItemById": {"item": {"id": "item-id"}}}}
PROJECT_FIELDS = {
    "data": {"status": {"projectV2Item": {"id": "item-id"}}, "kind": {"projectV2Item": {"id": "item-id"}}}
}


def github_response(value):
    return io.BytesIO(json.dumps(value).encode())


def github_success(request, **kwargs):
    if request.full_url.endswith("/issues"):
        return github_response(ISSUE)

    query = json.loads(request.data)["query"]

    if "query SupportProject" in query:
        return github_response(PROJECT)

    if "mutation AddSupportToProject" in query:
        return github_response(PROJECT_ITEM)

    if "mutation RouteSupport" in query:
        return github_response(PROJECT_FIELDS)

    raise AssertionError(f"Unexpected GitHub request: {request.full_url}")


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setattr(get_settings(), "support_github_token", SecretStr("test-server-secret"))
    connection = Mock(side_effect=github_success)
    monkeypatch.setattr(support, "urlopen", connection)
    return connection


@pytest.mark.parametrize("kind", ["bug", "feature"])
async def test_guest_submission_creates_client_issue_without_forwarding_identity(github, kind):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps({**PAYLOAD, "kind": kind})},
            headers={"Cookie": "private-session", "Authorization": "Bearer private-user-token"},
        )

    assert response.status_code == 201
    assert response.json() == {"number": 42, "url": "https://github.com/PapyrusReader/client/issues/42"}
    request = github.call_args_list[1].args[0]
    assert request.full_url == "https://api.github.com/repos/PapyrusReader/client/issues"
    assert request.get_header("Authorization") == "Bearer test-server-secret"
    assert request.get_header("Cookie") is None
    posted = json.loads(request.data)
    assert PAYLOAD["description"] in posted["body"]
    assert PAYLOAD["app_version"] in posted["body"]
    prefix = "BUG" if kind == "bug" else "FEATURE"
    assert posted["title"] == f"[{prefix}] {PAYLOAD['title']}"
    assert posted["labels"] == ["bug" if kind == "bug" else "enhancement"]
    assert posted["type"] == ("Bug" if kind == "bug" else "Feature")
    assert posted["assignees"] == ["Eoic"]
    assert "milestone" not in posted
    project_request = json.loads(github.call_args_list[2].args[0].data)
    assert project_request["variables"] == {"project": "project-id", "issue": "issue-id"}
    field_request = json.loads(github.call_args_list[3].args[0].data)

    assert field_request["variables"] == {
        "project": "project-id",
        "item": "item-id",
        "status": "status-field",
        "statusOption": "backlog-option",
        "kind": "kind-field",
        "kindOption": "task-option",
    }

    assert "Submitted from Papyrus." not in posted["body"]
    assert "test-server-secret" not in response.text


async def test_disabled_support_does_not_contact_github(monkeypatch):
    monkeypatch.setattr(get_settings(), "support_github_token", None)
    connection = Mock()
    monkeypatch.setattr(support, "urlopen", connection)

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
        )

    assert response.status_code == 503
    connection.assert_not_called()


@pytest.mark.parametrize(
    "changes", [{"kind": "other"}, {"title": "  "}, {"description": "x" * 8001}, {"repository": "other/repo"}]
)
async def test_invalid_submission_is_rejected_before_github(github, changes):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps({**PAYLOAD, **changes})}
        )

    assert response.status_code == 422
    github.assert_not_called()


async def test_support_rate_limits_guest_submissions(github):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        responses = [
            await client.post(f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)})
            for _ in range(4)
        ]

    assert [response.status_code for response in responses] == [201, 201, 201, 429]
    assert sum(call.args[0].full_url.endswith("/issues") for call in github.call_args_list) == 3


@pytest.mark.parametrize(
    "error,status",
    [(TimeoutError(), 504), (HTTPError("https://api.github.com", 403, "secret upstream error", {}, None), 502)],
)
async def test_provider_failure_is_sanitized_and_never_retried(github, error, status):
    github.side_effect = error

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
        )

    assert response.status_code == status
    assert "secret upstream error" not in response.text
    github.assert_called_once()


async def test_malformed_github_success_is_an_uncertain_submission(github):
    github.side_effect = [github_response(PROJECT), io.BytesIO(b'{"number": false}')]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
        )

    assert response.status_code == 504
    assert response.json()["detail"] == "Submission failed."
    assert github.call_count == 2


PNG = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"video-bytes"
ASSET_URL = "https://github.com/user-attachments/assets/11111111-2222-3333-4444-555555555555"


async def test_media_is_uploaded_to_github_and_embedded_in_issue(github):
    github.side_effect = [
        github_response(PROJECT),
        io.BytesIO(b'{"id": 123}'),
        io.BytesIO(json.dumps({"url": ASSET_URL}).encode()),
        io.BytesIO(json.dumps({"url": ASSET_URL}).encode()),
        github_response(ISSUE),
        github_response(PROJECT_ITEM),
        github_response(PROJECT_FIELDS),
    ]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps(PAYLOAD)},
            files=[("attachments", ("private-screenshot.png", PNG)), ("attachments", ("demo.mp4", MP4))],
        )

    assert response.status_code == 201
    requests = [call.args[0] for call in github.call_args_list[1:5]]
    assert requests[0].full_url == "https://api.github.com/repos/PapyrusReader/client"
    assert requests[1].full_url.startswith("https://uploads.github.com/user-attachments/assets?")
    assert "repository_id=123" in requests[1].full_url
    assert "name=attachment-1.png" in requests[1].full_url
    assert requests[1].data == PNG
    assert requests[2].data == MP4
    assert requests[1].get_header("Content-type") == "application/octet-stream"
    body = json.loads(requests[-1].data)["body"]
    assert f"![attachment-1.png]({ASSET_URL})" in body
    assert f"\n\n{ASSET_URL}" in body
    assert "private-screenshot" not in body


@pytest.mark.parametrize(
    "files,status",
    [
        ([("attachments", ("screen.png", PNG))] * 6, 422),
        ([("attachments", ("screen.svg", b"<svg/>"))], 422),
        ([("attachments", ("screen.png", b"<html>not an image</html>"))], 422),
        ([("attachments", ("empty.mp4", b""))], 413),
        ([("attachments", ("large.png", PNG + b"x" * (10 * 1024 * 1024)))], 413),
    ],
)
async def test_invalid_media_is_rejected_before_any_github_upload(github, files, status):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps(PAYLOAD)},
            files=files,
        )

    assert response.status_code == status
    github.assert_not_called()


async def test_failed_attachment_upload_never_creates_text_only_issue(github):
    github.side_effect = [github_response(PROJECT), io.BytesIO(b'{"id": 123}'), TimeoutError()]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps(PAYLOAD)},
            files=[("attachments", ("screen.png", PNG))],
        )

    assert response.status_code == 504
    assert github.call_count == 3


async def test_attachment_response_cannot_inject_markdown_or_external_urls(github):
    github.side_effect = [
        github_response(PROJECT),
        io.BytesIO(b'{"id": 123}'),
        io.BytesIO(b'{"url": "https://evil.example/image"}'),
    ]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps(PAYLOAD)},
            files=[("attachments", ("screen.png", PNG))],
        )

    assert response.status_code == 502
    assert github.call_count == 3


async def test_body_is_bounded_even_without_content_length(github, monkeypatch):
    from papyrus.api.routes import support as support_routes

    monkeypatch.setattr(support_routes, "MAX_REQUEST_BYTES", 32)

    async def body():
        yield b"--boundary\r\nContent-Disposition: form-data; name=payload\r\n\r\n"
        yield b"x" * 64
        yield b"\r\n--boundary--\r\n"

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            content=body(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        )

    assert response.status_code == 413
    github.assert_not_called()


async def test_rate_limit_rejects_upload_without_reading_body(github):
    async def unread_body():
        pytest.fail("A rate-limited request must not consume attachment bytes")
        yield b""

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        for _ in range(3):
            response = await client.post(
                f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
            )
            assert response.status_code == 201

        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            content=unread_body(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        )

    assert response.status_code == 429
    assert sum(call.args[0].full_url.endswith("/issues") for call in github.call_args_list) == 3


@pytest.mark.parametrize("failure", ["permissions", "missing-option", "graphql"])
async def test_project_configuration_is_checked_before_creating_an_issue_or_uploading(github, failure):
    project = deepcopy(PROJECT)

    if failure == "permissions":
        project["data"]["organization"]["projectV2"]["viewerCanUpdate"] = False
    elif failure == "missing-option":
        project["data"]["organization"]["projectV2"]["status"]["options"] = []
    else:
        project = {"errors": [{"message": "private upstream details"}]}

    github.side_effect = [github_response(project)]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues",
            data={"payload": json.dumps(PAYLOAD)},
            files=[("attachments", ("screen.png", PNG))],
        )

    assert response.status_code in (502, 503)
    assert response.json()["detail"] == "Submission failed."
    github.assert_called_once()


@pytest.mark.parametrize("stage", ["add", "fields"])
@pytest.mark.parametrize("failure", ["timeout", "graphql", "forbidden"])
async def test_project_failure_after_creation_keeps_success_and_logs_issue_for_reconciliation(
    github, caplog, stage, failure
):
    responses = [github_response(PROJECT), github_response(ISSUE)]

    if stage == "fields":
        responses.append(github_response(PROJECT_ITEM))

    if failure == "timeout":
        responses.append(TimeoutError())
    elif failure == "forbidden":
        responses.append(
            github_response(
                {
                    "data": {"addProjectV2ItemById": None},
                    "errors": [{"type": "FORBIDDEN", "message": "private upstream details"}],
                }
            )
        )
    else:
        responses.append(github_response({"errors": [{"message": "private upstream details"}]}))

    github.side_effect = responses

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
        )

    assert response.status_code == 201
    assert response.json()["number"] == 42
    assert sum(call.args[0].full_url.endswith("/issues") for call in github.call_args_list) == 1
    assert github.call_count == len(responses)
    assert "PapyrusReader/client#42" in caplog.text
    assert "private upstream details" not in caplog.text
    assert "test-server-secret" not in caplog.text

    if failure == "forbidden":
        assert "SUPPORT_GITHUB_TOKEN organization Projects write permission" in caplog.text


async def test_issue_creation_failure_is_not_retried_or_added_to_project(github):
    github.side_effect = [github_response(PROJECT), TimeoutError()]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/support/issues", data={"payload": json.dumps(PAYLOAD)}
        )

    assert response.status_code == 504
    assert github.call_count == 2


@pytest.mark.parametrize("kind", ["bug", "feature"])
async def test_support_routing_uses_server_environment(github, monkeypatch, kind):
    overrides = {
        "support_github_repository": "ExampleOrg/reader-support",
        "support_github_assignee": "maintainer",
        "support_github_project_owner": "ExampleOrg",
        "support_github_project_number": "7",
        "support_github_bug_label": "defect",
        "support_github_feature_label": "suggestion",
        "support_github_bug_type": "Problem",
        "support_github_feature_type": "Idea",
        "support_github_status_field": "Workflow",
        "support_github_status": "Triage",
        "support_github_kind_field": "Category",
        "support_github_kind": "Feedback",
    }

    for name, value in overrides.items():
        monkeypatch.setenv(name.upper(), value)

    settings = Settings(**get_settings().model_dump(include=set(Settings.model_fields) - set(overrides)))
    monkeypatch.setattr(support, "get_settings", lambda: settings)
    project = deepcopy(PROJECT)
    project["data"]["organization"]["projectV2"]["status"]["options"][0]["name"] = "Triage"
    project["data"]["organization"]["projectV2"]["kind"]["options"][0]["name"] = "Feedback"

    github.side_effect = [
        github_response(project),
        github_response({"id": 123}),
        github_response({"url": ASSET_URL}),
        github_response(ISSUE),
        github_response(PROJECT_ITEM),
        github_response(PROJECT_FIELDS),
    ]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{settings.api_prefix}/support/issues",
            data={"payload": json.dumps({**PAYLOAD, "kind": kind})},
            files=[("attachments", ("screen.png", PNG))],
        )

    assert response.status_code == 201
    assert response.json()["url"] == "https://github.com/ExampleOrg/reader-support/issues/42"
    requests = [call.args[0] for call in github.call_args_list]

    assert json.loads(requests[0].data)["variables"] == {
        "owner": "ExampleOrg",
        "number": 7,
        "statusField": "Workflow",
        "kindField": "Category",
    }

    assert requests[1].full_url == "https://api.github.com/repos/ExampleOrg/reader-support"
    assert requests[3].full_url == "https://api.github.com/repos/ExampleOrg/reader-support/issues"
    posted = json.loads(requests[3].data)
    assert posted["assignees"] == ["maintainer"]
    assert posted["labels"] == ["defect" if kind == "bug" else "suggestion"]
    assert posted["type"] == ("Problem" if kind == "bug" else "Idea")
    assert json.loads(requests[5].data)["variables"]["statusOption"] == "backlog-option"
    assert json.loads(requests[5].data)["variables"]["kindOption"] == "task-option"


@pytest.mark.parametrize(
    "name,value",
    [
        ("support_github_repository", "https://example.com/repo"),
        ("support_github_repository", "ExampleOrg/../repo"),
        ("support_github_project_number", 0),
        ("support_github_assignee", ""),
        ("support_github_project_owner", "org/other"),
        ("support_github_status", ""),
    ],
)
def test_invalid_support_routing_configuration_is_rejected(name, value):
    values = get_settings().model_dump(include=set(Settings.model_fields))
    values[name] = value

    with pytest.raises(ValidationError) as error:
        Settings(**values)

    assert [entry["loc"] for entry in error.value.errors()] == [(name,)]
