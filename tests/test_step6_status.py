from reviewer.status import decide_status
from reviewer.schemas import Coverage, CheckResult


def test_status_is_incomplete_when_required_check_is_skipped():
    checks = [CheckResult(name="semantic:spec", required=True, status="skipped", mode="not_executed")]
    assert decide_status([], Coverage(), checks) == "INCOMPLETE"


def test_status_is_pass_only_when_all_required_checks_pass_and_coverage_is_reviewed():
    checks = [CheckResult(name="semantic:spec", required=True, status="passed", mode="exhaustive")]
    assert decide_status([], Coverage(), checks) == "PASS"
