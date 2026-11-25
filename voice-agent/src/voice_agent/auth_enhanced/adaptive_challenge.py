from __future__ import annotations

from enum import Enum
from typing import Any

from .risk_scoring import RiskScore, get_risk_scorer
from ..config import AppConfig
from ..util.time import now_ms
from .risk_scoring import RiskLevel


class SecurityAction(str, Enum):
    """Security actions based on risk assessment."""
    STANDARD = "standard"
    ENHANCED = "enhanced"
    MFA_REQUIRED = "mfa_required"
    MANUAL_REVIEW = "manual_review"
    REJECT = "reject"


class AdaptiveChallenge:
    """Manage adaptive authentication challenges based on risk scoring."""

    def __init__(self, config: AppConfig):
        """Initialize adaptive challenge manager.

        Args:
            config: Application configuration
        """
        self.config = config
        self.risk_scorer = get_risk_scorer(config)

    def get_security_action(self, risk_score: RiskScore, confidence_threshold: float = 0.5) -> SecurityAction:
        """Determine appropriate security action based on risk assessment.

        Args:
            risk_score: Calculated risk assessment
            confidence_threshold: Threshold for confidence-based actions

        Returns:
            Appropriate security action
        """
        if risk_score.risk_level == RiskLevel.CRITICAL:
            return SecurityAction.REJECT
        elif risk_score.risk_level == RiskLevel.HIGH:
            return SecurityAction.MFA_REQUIRED
        elif risk_score.risk_level == RiskLevel.MEDIUM:
            return SecurityAction.ENHANCED
        else:
            if risk_score.confidence_score < confidence_threshold:
                return SecurityAction.ENHANCED
            return SecurityAction.STANDARD

    def get_challenge_requirements(self, risk_score: RiskScore) -> dict[str, Any]:
        """Generate specific challenge requirements based on risk assessment.

        Args:
            risk_score: Calculated risk assessment

        Returns:
            Dictionary containing challenge requirements
        """
        requirements = {
            'name': 'voiceAuthentication',
            'version': '1.0',
            'requirements': [],
            'max_retries': 3,
            'timeout_seconds': 30,
        }

        risk_level = risk_score.risk_level

        if risk_level == RiskLevel.LOW:
            # Standard authentication only
            requirements['requirements'] = [
                {'type': 'voice', 'priority': 'high', 'timeout': 30},
            ]

        elif risk_level == RiskLevel.MEDIUM:
            # Enhanced verification
            requirements['requirements'] = [
                {'type': 'voice', 'priority': 'high', 'timeout': 45},
                {'type': 'enrollment_verification', 'priority': 'medium', 'timeout': 15},
                {'type': 'biometric_consent', 'priority': 'low', 'timeout': 10},
            ]

        elif risk_level == RiskLevel.HIGH:
            # Multi-factor authentication required
            requirements['requirements'] = [
                {'type': 'voice', 'priority': 'high', 'timeout': 60},
                {'type': 'mfa_code', 'priority': 'high', 'timeout': 30},
                {'type': 'device_verification', 'priority': 'medium', 'timeout': 20},
                {'type': 'security_question', 'priority': 'low', 'timeout': 15},
            ]

        else:  # CRITICAL
            # Reject or manual review
            requirements['action'] = 'manual_review'
            requirements['auto_reject'] = False
            requirements['notify_security_team'] = True

        # Add fraud indicators to requirements
        if risk_score.fraud_indicators:
            requirements['fraud_indicators'] = risk_score.fraud_indicators

        # Add recommendations as warnings
        if risk_score.recommendations:
            requirements['warnings'] = risk_score.recommendations

        return requirements

    def should_enforce_challenge(self,
                                risk_score: RiskScore,
                                previous_attempts: list[dict[str, Any]] = None) -> tuple[bool, str]:
        """Determine if enhanced challenge enforcement is needed.

        Args:
            risk_score: Current risk assessment
            previous_attempts: List of previous authentication attempts

        Returns:
            Tuple of (should_enforce, reason)
        """
        if previous_attempts is None:
            previous_attempts = []

        recent_failures = [attempt for attempt in previous_attempts
                          if attempt.get('success') is False and
                          now_ms() - attempt.get('timestamp', 0) < 300000]  # Last 5 minutes

        if risk_score.risk_level == RiskLevel.CRITICAL:
            return True, "Critical risk level detected - MFA required"

        if risk_score.risk_level == RiskLevel.HIGH and recent_failures:
            return True, f"High risk level with {len(recent_failures)} recent failures - MFA required"

        if risk_score.fraud_indicators:
            fraud_indicators = risk_score.fraud_indicators
            if 'suspicious_device_id' in fraud_indicators:
                return True, "Suspicious device detected - Enhanced verification required"
            if 'suspicious_ip_pattern' in fraud_indicators:
                return True, "Suspicious IP pattern detected - Enhanced verification required"

        return False, "Risk level allows standard authentication"

    def get_recommendation_for_user(self, risk_score: RiskScore) -> str:
        """Generate user-friendly recommendation based on risk assessment.

        args:
            risk_score: Calculated risk assessment

        Returns:
            User-friendly recommendation string
        """
        risk_level = risk_score.risk_level

        recommendations = {
            RiskLevel.LOW: "Standard authentication accepted",
            RiskLevel.MEDIUM: "Please verify your voice clearly and wait for additional confirmation",
            RiskLevel.HIGH: "Additional verification required - please provide MFA code or complete security challenge",
            RiskLevel.CRITICAL: "Authentication denied due to security concerns - contact support if this is an error",
        }

        base_message = recommendations.get(risk_level, "Authentication processing...")

        if risk_score.fraud_indicators:
            fraud_messages = {
                'suspicious_device_id': "We've detected a suspicious device. Please verify your identity.",
                'suspicious_ip_pattern': "We've detected unusual login activity. Please verify your identity.",
                'poor_audio_quality': "Voice quality issues detected. Please try again in a quiet environment.",
                'inadequate_playback': "Voice authentication issues detected. Please try again.",
            }

            for indicator in risk_score.fraud_indicators:
                if indicator in fraud_messages:
                    base_message += f" {fraud_messages[indicator]}"

        return base_message

    def log_challenge_outcome(self,
                             risk_score: RiskScore,
                             challenge_result: dict[str, Any],
                             success: bool) -> None:
        """Log challenge outcome for analysis and improvement.

        Args:
            risk_score: Initial risk assessment
            challenge_result: Challenge requirements and results
            success: Whether authentication was successful
        """
        outcome_log = {
            'timestamp': now_ms(),
            'initial_risk': risk_score.risk_level,
            'risk_score': risk_score.confidence_score,
            'challenge_type': challenge_result.get('name', 'voiceAuthentication'),
            'requirements_met': challenge_result.get('requirements', []),
            'success': success,
            'fraud_indicators_detected': risk_score.fraud_indicators,
        }

        # In production, this would log to a structured logging system
        # For now, we'll use print for demonstration
        print(f"[ADAPTIVE_CHALLENGE] {outcome_log}")

    def should_extend_session(self, risk_score: RiskScore) -> bool:
        """Determine if session should be extended based on risk assessment.

        Args:
            risk_score: Current risk assessment

        Returns:
            Whether session should be extended
        """
        if risk_score.risk_level == RiskLevel.CRITICAL:
            return False
        elif risk_score.risk_level == RiskLevel.HIGH:
            return True
        elif risk_score.risk_level == RiskLevel.MEDIUM:
            return True
        else:
            return True  # Standard authentication allows normal session extension

    def get_session_security_level(self, risk_score: RiskScore) -> str:
        """Get session security level based on risk assessment.

        Args:
            risk_score: Current risk assessment

        Returns:
            Session security level
        """
        risk_levels = {
            RiskLevel.LOW: 'standard',
            RiskLevel.MEDIUM: 'enhanced',
            RiskLevel.HIGH: 'high',
            RiskLevel.CRITICAL: 'revoked',
        }
        return risk_levels.get(risk_score.risk_level, 'standard')
