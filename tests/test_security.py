from hermes.server.models import ApprovalDecision
from hermes.security.approval_manager import NaturalLanguageApprovalParser
from hermes.security.policy_engine import PolicyDecision, PolicyEngine
from hermes.config.settings import RiskLevel


class TestPolicyEngine:
    def test_read_only_allowed(self):
        engine = PolicyEngine()
        result = engine.evaluate("get_system_info")
        assert result.decision == PolicyDecision.ALLOW
        assert result.risk_level == RiskLevel.READ_ONLY

    def test_screenshot_read_only(self):
        engine = PolicyEngine()
        result = engine.evaluate("screenshot")
        assert result.decision == PolicyDecision.ALLOW
        assert result.risk_level == RiskLevel.READ_ONLY

    def test_open_app_low_risk(self):
        engine = PolicyEngine()
        result = engine.evaluate("open_app", {"app": "chrome"})
        assert result.decision == PolicyDecision.ALLOW
        assert result.risk_level == RiskLevel.LOW_RISK

    def test_click_requires_approval(self):
        engine = PolicyEngine()
        result = engine.evaluate("click", {"x": 10, "y": 20})
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL

    def test_high_risk_requires_approval(self):
        engine = PolicyEngine()
        result = engine.evaluate("delete_file")
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL
        assert result.risk_level == RiskLevel.HIGH_RISK

    def test_denied_tool(self):
        engine = PolicyEngine()
        result = engine.evaluate("format_disk")
        assert result.decision == PolicyDecision.DENY


class TestNaturalLanguageApprovalParser:
    def test_approve_all_turkish(self):
        result = NaturalLanguageApprovalParser.parse("Tamam, hepsini onayliyorum", total_steps=4)
        assert result.decision == ApprovalDecision.APPROVE_ALL

    def test_cancel(self):
        result = NaturalLanguageApprovalParser.parse("Iptal et", total_steps=4)
        assert result.decision == ApprovalDecision.CANCEL

    def test_partial_steps(self):
        result = NaturalLanguageApprovalParser.parse("Sadece 1 ve 3'u yap", total_steps=4)
        assert result.decision == ApprovalDecision.PARTIAL
        assert result.approved_steps == [1, 3]

    def test_exclude_step(self):
        result = NaturalLanguageApprovalParser.parse("2. adimi cikar", total_steps=4)
        assert result.decision == ApprovalDecision.PARTIAL
        assert 2 in (result.excluded_steps or [])
