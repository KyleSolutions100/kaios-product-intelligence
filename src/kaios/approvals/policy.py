"""Deterministic, default-deny action risk policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from kaios.core.contracts import ActionProposal, RiskClassification

from .contracts import PolicyDecision, PolicyOutcome


LOW_RISK_READ_ONLY_ACTIONS = frozenset(
    {"read_marketplace_data", "read_product_data", "generate_draft_report"}
)

RISKY_ACTION_TYPES = frozenset(
    {
        "spend_money",
        "publish_listing",
        "publish_content",
        "modify_etsy_data",
        "modify_shopify_data",
        "modify_supplier_data",
        "modify_marketplace_data",
        "start_advertisement",
        "send_external_message",
        "financial_transfer",
        "delete_record",
    }
)


@dataclass(frozen=True)
class PolicyConfig:
    low_risk_allowlist: frozenset[str] = LOW_RISK_READ_ONLY_ACTIONS
    always_require_approval: frozenset[str] = RISKY_ACTION_TYPES
    paid_ai_action_types: frozenset[str] = frozenset(
        {"use_paid_ai", "paid_ai_usage"}
    )
    paid_ai_approval_threshold: Decimal = Decimal("0")
    blocked_action_types: frozenset[str] = frozenset()
    workspace_blocked_actions: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    workspace_approval_actions: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def evaluate(self, proposal: ActionProposal) -> PolicyDecision:
        action = proposal.action_type
        workspace_blocked = self.config.workspace_blocked_actions.get(
            proposal.workspace_id, frozenset()
        )
        if action in self.config.blocked_action_types or action in workspace_blocked:
            return PolicyDecision(
                workspace_id=proposal.workspace_id,
                proposal_id=proposal.proposal_id,
                outcome=PolicyOutcome.BLOCKED,
                risk=RiskClassification.CRITICAL,
                reasons=["action is blocked by configured policy"],
                requires_approval=False,
                allowed=False,
            )

        reasons: list[str] = []
        risk = proposal.risk
        workspace_approval = self.config.workspace_approval_actions.get(
            proposal.workspace_id, frozenset()
        )
        if action in self.config.always_require_approval:
            reasons.append("action type requires human approval")
            risk = _higher_risk(risk, RiskClassification.HIGH)
        if action in workspace_approval:
            reasons.append("workspace policy requires human approval")
        if proposal.estimated_cost > 0:
            reasons.append("action has an estimated monetary cost")
            risk = _higher_risk(risk, RiskClassification.HIGH)
        if (
            action in self.config.paid_ai_action_types
            and proposal.estimated_cost > self.config.paid_ai_approval_threshold
        ):
            reasons.append("paid AI cost is above the configured threshold")
            risk = _higher_risk(risk, RiskClassification.HIGH)
        if proposal.is_public:
            reasons.append("action has a public effect")
            risk = _higher_risk(risk, RiskClassification.HIGH)
        if not proposal.is_reversible:
            reasons.append("action is irreversible or difficult to reverse")
            risk = _higher_risk(risk, RiskClassification.CRITICAL)
        if proposal.risk is not RiskClassification.LOW:
            reasons.append("proposal declares elevated risk")

        explicitly_safe = (
            action in self.config.low_risk_allowlist
            and proposal.estimated_cost == 0
            and not proposal.is_public
            and proposal.is_reversible
            and proposal.risk is RiskClassification.LOW
            and action not in workspace_approval
        )
        if explicitly_safe and not reasons:
            return PolicyDecision(
                workspace_id=proposal.workspace_id,
                proposal_id=proposal.proposal_id,
                outcome=PolicyOutcome.APPROVAL_NOT_REQUIRED,
                risk=RiskClassification.LOW,
                reasons=["action is explicitly allowlisted as low-risk and read-only"],
                requires_approval=False,
                allowed=True,
            )

        if action not in self.config.low_risk_allowlist and not reasons:
            reasons.append("unknown action type is not explicitly allowlisted")
            risk = _higher_risk(risk, RiskClassification.MEDIUM)
        return PolicyDecision(
            workspace_id=proposal.workspace_id,
            proposal_id=proposal.proposal_id,
            outcome=PolicyOutcome.APPROVAL_REQUIRED,
            risk=risk,
            reasons=reasons,
            requires_approval=True,
            allowed=True,
        )


_RISK_ORDER = {
    RiskClassification.LOW: 0,
    RiskClassification.MEDIUM: 1,
    RiskClassification.HIGH: 2,
    RiskClassification.CRITICAL: 3,
}


def _higher_risk(
    current: RiskClassification, minimum: RiskClassification
) -> RiskClassification:
    return current if _RISK_ORDER[current] >= _RISK_ORDER[minimum] else minimum
