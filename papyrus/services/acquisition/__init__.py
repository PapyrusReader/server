"""Stable acquisition service imports; implementations live in focused modules."""

from .jobs import (
    build_test_endpoint,
    cancel_job,
    delete_acquisition_endpoint,
    delete_terminal_job,
    job_file_candidates,
    owned_endpoint,
    owned_job,
    paginated_jobs,
    retry_job_import,
    run_enabled_rules,
    run_rule,
    select_job_file,
    submit_release_batch,
)
from .providers import (
    QbittorrentClient,
    dispatch_arr_command,
    search_endpoint,
    submit_to_client,
    test_endpoint_connection,
)
from .release_tokens import create_release_token, decode_release_token
from .types import (
    BatchSubmissionResult,
    JobFileCandidate,
    QbittorrentFile,
    QbittorrentTorrent,
    ReleaseCandidate,
    ReleaseTokenPayload,
)

__all__ = [
    "ReleaseCandidate",
    "ReleaseTokenPayload",
    "BatchSubmissionResult",
    "QbittorrentTorrent",
    "QbittorrentFile",
    "JobFileCandidate",
    "create_release_token",
    "decode_release_token",
    "QbittorrentClient",
    "search_endpoint",
    "submit_to_client",
    "dispatch_arr_command",
    "test_endpoint_connection",
    "owned_endpoint",
    "owned_job",
    "paginated_jobs",
    "job_file_candidates",
    "select_job_file",
    "cancel_job",
    "delete_terminal_job",
    "retry_job_import",
    "submit_release_batch",
    "delete_acquisition_endpoint",
    "build_test_endpoint",
    "run_rule",
    "run_enabled_rules",
]
