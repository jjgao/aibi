"""The tests D422 cites as pins, each with the expressions it must assert (SPEC D422).

``tests/core/pins.py`` guards them and reads this table; the guard's own tests (``test_pins.py``)
give it a table of their own."""

from aibi.core.api import errors
from aibi.core.mcp.calls import PER_CLIENT

PLACE = "tests/core/mcp/test_place_policy.py"
TRANSPORT = "tests/core/mcp/test_transport.py"
PAGE = "tests/core/mcp/test_page.py"
WRITES = "tests/core/catalog/test_validate_writes_nothing.py"
PINS: dict[str, tuple[str, ...]] = {
    f"{TRANSPORT}::test_one_client_runs_at_most_its_share_of_the_places_and_others_still_get_one": (
        f"(share.code, share.limit.max, status_of(share)) == ('LIMIT_EXCEEDED', {PER_CLIENT}, 503)",
        "sorted((r.limit.name for r in refused)) == "
        "['client_tool_calls', 'tool_seconds', 'tool_seconds']",
    ),
    f"{TRANSPORT}::test_clients_are_counted_by_the_key_of_their_address": (
        "refused[0].limit.name == 'client_tool_calls'",
    ),
    f"{PAGE}::test_a_call_that_gets_no_place_is_a_page_with_retry_after_and_its_limit": (
        "answered.status_code == 503",
        f"answered.headers['retry-after'] == '{errors.RETRY_IMPORT}'",
        f"'Limit: client_tool_calls, at most {PER_CLIENT}' in parsed(answered.text).text",
    ),
    f"{PLACE}::test_a_json_tool_call_with_no_place_is_a_503_naming_client_tool_calls_with_retry_after": (  # noqa: E501
        f"limit_of(third) == (503, 'client_tool_calls', PER_CLIENT, '{errors.RETRY_IMPORT}')",
        "third.headers['retry-after'] == str(errors.RETRY_IMPORT)",
    ),
    f"{PLACE}::test_a_json_tool_call_with_every_place_held_is_a_503_naming_tool_calls_with_retry_after": (  # noqa: E501
        f"limit_of(ninth) == (503, 'tool_calls', CONCURRENT, '{errors.RETRY_IMPORT}')",
        "ninth.headers['retry-after'] == str(errors.RETRY_IMPORT)",
    ),
    f"{PLACE}::test_after_tool_seconds_a_json_call_is_a_503_and_its_place_may_still_be_held": (
        f"limit_of(answered) == (503, 'tool_seconds', 1, '{errors.RETRY_IMPORT}')",
        "limit_of(served.api('search_catalog', {})) == "
        f"(503, 'client_tool_calls', 2, '{errors.RETRY_IMPORT}')",
    ),
    f"{PLACE}::test_the_servers_own_request_limit_is_a_503_without_retry_after_on_every_path": (
        "status == 503",
        "headers['content-type'].startswith('text/plain')",
        "'retry-after' not in headers",
        "body == b'Service Unavailable'",
    ),
    f"{PLACE}::test_an_mcp_call_with_no_place_is_a_200_tool_error_without_retry_after": (
        "answered.status_code == 200",
        "'retry-after' not in answered.headers",
        "result['isError'] is True",
        "refusal['limit'] == {'name': 'client_tool_calls', 'max': PER_CLIENT}",
    ),
    f"{PLACE}::test_an_mcp_body_of_inline_bytes_is_inline_and_one_byte_more_is_not": (
        "inline.status_code == 200",
        "inline.json()['result']['isError'] is True",
        "'retry-after' not in inline.headers",
        "beyond.status_code != 200",
    ),
    f"{WRITES}::test_validate_document_changes_no_row_and_no_file": (
        "after_rows == before_rows",
        "after_files == before_files",
    ),
}
"""The tests D422 cites, each with the expressions it must assert."""
