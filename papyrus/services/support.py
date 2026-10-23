"""Submit user-reviewed reports using server-controlled GitHub routing and credentials."""

import json
import logging
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import BackgroundTasks, HTTPException

from papyrus.config import get_settings
from papyrus.schemas.support import SupportRequest, SupportResponse
from papyrus.services.support_attachments import SupportAttachment

logger = logging.getLogger(__name__)

PROJECT_QUERY = """
query SupportProject($owner: String!, $number: Int!, $statusField: String!, $kindField: String!) {
  organization(login: $owner) {
    projectV2(number: $number) {
      id
      viewerCanUpdate
      status: field(name: $statusField) {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
      kind: field(name: $kindField) {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class SupportProject:
    id: str
    status_field: str
    status_option: str
    kind_field: str
    kind_option: str


def submit_support(
    payload: SupportRequest,
    attachments: list[SupportAttachment],
    background_tasks: BackgroundTasks,
) -> SupportResponse:
    settings = get_settings()
    token = settings.support_github_token
    repository = settings.support_github_repository

    if token is None:
        raise HTTPException(status_code=503, detail="Support submissions are not configured on this server.")

    if payload.kind == "bug":
        category = "Bug report"
        prefix = "BUG"
        label = settings.support_github_bug_label
        issue_type = settings.support_github_bug_type
    else:
        category = "Feature request"
        prefix = "FEATURE"
        label = settings.support_github_feature_label
        issue_type = settings.support_github_feature_type

    body = (
        f"## {category}\n\n{payload.description}\n\n"
        f"## App details\n\nVersion: {payload.app_version}\nPlatform: {payload.platform}"
    )

    headers = {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "Papyrus-Support",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    project = load_support_project(headers)

    if attachments:
        repository_details = github_request(Request(f"https://api.github.com/repos/{repository}", headers=headers))
        repository_id = repository_details.get("id")

        if type(repository_id) is not int or repository_id <= 0:
            raise HTTPException(status_code=502, detail="Submission failed.")

        body += "\n\n## Attachments"

        for attachment in attachments:
            query = urlencode(
                {
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "repository_id": repository_id,
                }
            )

            asset = github_request(
                Request(
                    f"https://uploads.github.com/user-attachments/assets?{query}",
                    data=attachment.data,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    method="POST",
                )
            )

            url = asset.get("url")

            if not isinstance(url, str) or not re.fullmatch(
                r"https://github\.com/user-attachments/assets/[a-zA-Z0-9-]+", url
            ):
                raise HTTPException(status_code=502, detail="Submission failed.")

            if attachment.content_type.startswith("image/"):
                body += f"\n\n![{attachment.name}]({url})"
            else:
                body += f"\n\n{url}"

    result = github_request(
        Request(
            f"https://api.github.com/repos/{repository}/issues",
            data=json.dumps(
                {
                    "title": f"[{prefix}] {payload.title}",
                    "body": body,
                    "labels": [label],
                    "type": issue_type,
                    "assignees": [settings.support_github_assignee],
                }
            ).encode(),
            headers=headers,
            method="POST",
        )
    )

    number = result.get("number")

    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise HTTPException(status_code=504, detail="Submission failed.")

    background_tasks.add_task(route_support_issue, headers, project, result.get("node_id"), repository, number)
    return SupportResponse(number=number, url=f"https://github.com/{repository}/issues/{number}")


def route_support_issue(
    headers: dict[str, str],
    project: SupportProject,
    issue_id: object,
    repository: str,
    number: int,
) -> None:
    """Apply best-effort project metadata after the confirmed issue response has been sent."""
    try:
        add_support_to_project(headers, project, required_id(issue_id))
    except HTTPException as error:
        logger.error(
            "Support issue %s#%s was created but project routing failed: %s Reconcile its project fields.",
            repository,
            number,
            error.detail,
        )


def required_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="Submission failed.")

    return value


def required_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=502, detail="Submission failed.")

    return value


def project_selection(value: object, option_name: str) -> tuple[str, str]:
    field = required_object(value)
    options = field.get("options")

    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and option.get("name") == option_name:
                return required_id(field.get("id")), required_id(option.get("id"))

    raise HTTPException(status_code=502, detail="Submission failed.")


def load_support_project(headers: dict[str, str]) -> SupportProject:
    settings = get_settings()

    data = github_graphql(
        headers,
        PROJECT_QUERY,
        {
            "owner": settings.support_github_project_owner,
            "number": settings.support_github_project_number,
            "statusField": settings.support_github_status_field,
            "kindField": settings.support_github_kind_field,
        },
    )
    organization = required_object(data.get("organization"))
    project = required_object(organization.get("projectV2"))

    if project.get("viewerCanUpdate") is not True:
        raise HTTPException(status_code=503, detail="Submission failed.")

    status_field, status_option = project_selection(project.get("status"), settings.support_github_status)
    kind_field, kind_option = project_selection(project.get("kind"), settings.support_github_kind)

    return SupportProject(
        id=required_id(project.get("id")),
        status_field=status_field,
        status_option=status_option,
        kind_field=kind_field,
        kind_option=kind_option,
    )


def add_support_to_project(headers: dict[str, str], project: SupportProject, issue_id: str) -> None:
    """Only mutate project metadata after creation, so a routing failure cannot invite duplicate reports."""
    data = github_graphql(
        headers,
        """
        mutation AddSupportToProject($project: ID!, $issue: ID!) {
          addProjectV2ItemById(input: {projectId: $project, contentId: $issue}) { item { id } }
        }
        """,
        {"project": project.id, "issue": issue_id},
    )

    added = required_object(data.get("addProjectV2ItemById"))
    item = required_object(added.get("item"))

    github_graphql(
        headers,
        """
        mutation RouteSupport(
          $project: ID!, $item: ID!, $status: ID!, $statusOption: String!, $kind: ID!, $kindOption: String!
        ) {
          status: updateProjectV2ItemFieldValue(input: {
            projectId: $project, itemId: $item, fieldId: $status, value: {singleSelectOptionId: $statusOption}
          }) { projectV2Item { id } }
          kind: updateProjectV2ItemFieldValue(input: {
            projectId: $project, itemId: $item, fieldId: $kind, value: {singleSelectOptionId: $kindOption}
          }) { projectV2Item { id } }
        }
        """,
        {
            "project": project.id,
            "item": required_id(item.get("id")),
            "status": project.status_field,
            "statusOption": project.status_option,
            "kind": project.kind_field,
            "kindOption": project.kind_option,
        },
    )


def github_graphql(headers: dict[str, str], query: str, variables: dict[str, object]) -> dict[str, object]:
    result = github_request(
        Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers=headers,
            method="POST",
        )
    )

    errors = result.get("errors")

    if isinstance(errors, list) and any(
        isinstance(error, dict) and error.get("type") == "FORBIDDEN" for error in errors
    ):
        raise HTTPException(
            status_code=502,
            detail="GitHub denied project access. Check SUPPORT_GITHUB_TOKEN organization Projects write permission.",
        )

    if errors:
        raise HTTPException(status_code=502, detail="Submission failed.")

    return required_object(result.get("data"))


def github_request(request: Request) -> dict[str, object]:
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code >= 500:
            raise HTTPException(
                status_code=504,
                detail="Submission could not be confirmed. Check the repository before submitting again.",
            ) from error

        raise HTTPException(
            status_code=502, detail="GitHub could not accept the submission. Please try later."
        ) from error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=504,
            detail="Submission could not be confirmed. Check the repository before submitting again.",
        ) from error

    if not isinstance(result, dict):
        raise HTTPException(status_code=504, detail="Submission failed.")

    return result
