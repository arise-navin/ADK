import pytest

from servicenow_client import ServiceNowRequestError, build_configuration_item_list


SYS_ID = "8296b5ae0ad3370227dac480f0fc66dd"


def test_reference_fields_use_values() -> None:
    result = build_configuration_item_list({"result": [{"sys_id": {"value": SYS_ID, "display_value": "CI"}, "name": "CI", "location": {"value": "Location", "link": "https://example.com"}}]})
    assert result.items[0].sys_id == SYS_ID
    assert result.items[0].location == "Location"


@pytest.mark.parametrize("record", [
    {"name": "missing ID"},
    {"sys_id": "malformed"},
    {"sys_id": {"display_value": SYS_ID}},
    {"sys_id": SYS_ID, "location": {"value": 4}},
])
def test_malformed_records_fail(record: dict[str, object]) -> None:
    with pytest.raises(ServiceNowRequestError):
        build_configuration_item_list({"result": [record]})
