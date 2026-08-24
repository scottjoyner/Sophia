#!/usr/bin/env python3
"""Audit logging for voice authentication system."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class LogLevel:
    INFO = "info"
    WARN = "warn"
    AUDIT = "audit"
    SECURITY = "security"

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice_agent.auth_enhanced.risk_scoring import RiskScore, RiskLevel


class AuditEventType(str, Enum):
    """Types of audit events."""
    AUTHENTICATION_ATTEMPT = "authentication_attempt"
    AUTHENTICATION_SUCCESS = "authentication_success"
    AUTHENTICATION_FAILURE = "authentication_failure"
    SECURITY_VIOLATION = "security_violation"
    FRAUD_DETECTION = "fraud_detection"
    RISK_ASSESSMENT = "risk_assessment"
    ACCESS_DENIED = "access_denied"
    MFA_COMPLETED = "mfa_completed"
    DEVICE_VERIFICATION = "device_verification"
    IP_VERIFICATION = "ip_verification"
    ENROLLMENT_STARTED = "enrollment_started"
    ENROLLMENT_COMPLETED = "enrollment_completed"
    ENROLLMENT_FAILED = "enrollment_failed"


@dataclass
class AuditLogEntry:
    """Audit log entry for comprehensive tracking."""
    timestamp: datetime
    event_type: AuditEventType
    severity: str
    user_id: str
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_fingerprint: Optional[str] = None
    risk_score: Optional[float] = None
    risk_level: Optional[str] = None
    fraud_indicators: Optional[list[str]] = None
    event_details: Optional[dict[str, Any]] = None
    outcome: Optional[str] = None
    actor: Optional[str] = None


class SecurityEventDetector:
    """Detect and log security events and violations."""

    def __init__(self, logger: logging.Logger):
        """Initialize security event detector.

        Args:
            logger: Logger instance for security events
        """
        self.logger = logger

    def detect_security_events(self,
                              risk_score: RiskScore,
                              context: dict[str, Any],
                              outcome: str) -> list[AuditEventType]:
        """Detect security events based on risk assessment and outcome.

        Args:
            risk_score: Risk assessment result
            context: Request context
            outcome: Authentication outcome

        Returns:
            List of detected security events
        """
        events = []

        # High-risk attempts
        if risk_score.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            if outcome == "failure":
                events.append(AuditEventType.SECURITY_VIOLATION)
            elif outcome == "success":
                events.append(AuditEventType.FRAUD_DETECTION)

        # Device fraud detection
        if "suspicious_device_id" in risk_score.fraud_indicators:
            events.append(AuditEventType.SECURITY_VIOLATION)

        # IP fraud detection
        if "suspicious_ip_pattern" in risk_score.fraud_indicators:
            events.append(AuditEventType.SECURITY_VIOLATION)

        # Poor audio quality indicators
        if "poor_audio_quality" in risk_score.fraud_indicators:
            events.append(AuditEventType.FRAUD_DETECTION)

        # Authentication attempts
        if outcome == "success":
            events.append(AuditEventType.AUTHENTICATION_SUCCESS)
        else:
            events.append(AuditEventType.AUTHENTICATION_FAILURE)

        # MFA completion
        if "mfa_completed" in outcome:
            events.append(AuditEventType.MFA_COMPLETED)

        return events

    def log_security_event(self,
                          event_type: AuditEventType,
                          user_id: str,
                          context: dict[str, Any],
                          details: Optional[dict[str, Any]] = None) -> None:
        """Log security event with appropriate level and formatting.

        Args:
            event_type: Type of security event
            user_id: User identifier
            context: Request context
            details: Additional event details
        """
        log_level = self._get_log_level(event_type)

        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            'log_level': log_level,
            'user_id': user_id,
            'ip_address': context.get('ip_address'),
            'user_agent': context.get('user_agent'),
            'device_id': context.get('device_id'),
            'risk_score': context.get('risk_score'),
            'fraud_indicators': context.get('fraud_indicators', []),
            'outcome': context.get('outcome'),
            'details': details or {},
        }

        if event_type in [AuditEventType.SECURITY_VIOLATION, AuditEventType.FRAUD_DETECTION]:
            self.logger.warning(f"SECURITY EVENT: {json.dumps(log_entry)}")
        elif event_type in [AuditEventType.AUTHENTICATION_SUCCESS, AuditEventType.MFA_COMPLETED]:
            self.logger.info(f"AUDIT EVENT: {json.dumps(log_entry)}")
        else:
            self.logger.debug(f"AUDIT EVENT: {json.dumps(log_entry)}")

    def _get_log_level(self, event_type: AuditEventType) -> str:
        """Get appropriate log level for event type."""
        if event_type in [
            AuditEventType.SECURITY_VIOLATION,
            AuditEventType.FRAUD_DETECTION,
            AuditEventType.ACCESS_DENIED
        ]:
            return LogLevel.AUDIT
        elif event_type == AuditEventType.AUTHENTICATION_FAILURE:
            return LogLevel.WARN
        else:
            return LogLevel.INFO


class RiskAuditLogger:
    """Comprehensive logging for risk-based authentication events."""

    def __init__(self, log_file: str = "risk_audit.log"):
        """Initialize audit logger.

        Args:
            log_file: Path to audit log file
        """
        self.log_file = Path(log_file)
        self.security_detector = SecurityEventDetector(logging.getLogger('security_audit'))
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log_authentication_attempt(self,
                                   user_id: str,
                                   context: dict[str, Any],
                                   risk_score: RiskScore,
                                   outcome: str,
                                   details: Optional[dict[str, Any]] = None) -> None:
        """Log authentication attempt with full context and risk assessment.

        Args:
            user_id: User identifier
            context: Request context including device, IP, timing
            risk_score: Risk assessment result
            outcome: Authentication outcome
            details: Additional event details
        """
        enriched_context = {
            **context,
            'risk_score': risk_score.confidence_score,
            'fraud_indicators': risk_score.fraud_indicators,
            'outcome': outcome,
        }

        security_events = self.security_detector.detect_security_events(
            risk_score, context, outcome
        )

        logged_events = set()
        for event_type in security_events:
            if event_type.value not in logged_events:
                self.security_detector.log_security_event(
                    event_type=event_type,
                    user_id=user_id,
                    context=enriched_context,
                    details=details or {}
                )
                logged_events.add(event_type.value)

        log_entry = AuditLogEntry(
            timestamp=datetime.utcnow(),
            event_type=AuditEventType.AUTHENTICATION_ATTEMPT,
            severity=self._get_severity_from_risk(risk_score, outcome),
            user_id=user_id,
            session_id=context.get('session_id'),
            ip_address=context.get('ip_address'),
            user_agent=context.get('user_agent'),
            device_fingerprint=context.get('device_id'),
            risk_score=risk_score.confidence_score,
            risk_level=risk_score.risk_level.value,
            fraud_indicators=risk_score.fraud_indicators,
            event_details=details,
            outcome=outcome,
            actor=context.get('actor')
        )

        self._write_log_entry(log_entry)

    def log_risk_assessment(self,
                           user_id: str,
                           risk_score: RiskScore,
                           context: dict[str, Any]) -> None:
        """Log risk assessment for analytics and monitoring.

        Args:
            user_id: User identifier
            risk_score: Risk assessment result
            context: Request context
        """
        details = {
            'risk_score': risk_score.confidence_score,
            'risk_level': risk_score.risk_level.value,
            'audio_risk': risk_score.audio_risk,
            'context_risk': risk_score.context_risk,
            'device_risk': risk_score.device_risk,
            'temporal_risk': risk_score.temporal_risk,
            'fraud_indicators': risk_score.fraud_indicators,
            'recommendations': risk_score.recommendations,
            'context_details': {
                'ip_address': context.get('ip_address'),
                'user_agent': context.get('user_agent'),
                'device_id': context.get('device_id'),
                'time_of_day': context.get('timestamp'),
            }
        }

        self._write_log_entry(AuditLogEntry(
            timestamp=datetime.utcnow(),
            event_type=AuditEventType.RISK_ASSESSMENT,
            severity=LogLevel.INFO,
            user_id=user_id,
            event_details=details
        ))

    def log_access_denied(self,
                         user_id: str,
                         context: dict[str, Any],
                         reason: str) -> None:
        """Log access denial with detailed reasoning.

        Args:
            user_id: User identifier
            context: Request context
            reason: Reason for access denial
        """
        self._write_log_entry(AuditLogEntry(
            timestamp=datetime.utcnow(),
            event_type=AuditEventType.ACCESS_DENIED,
            severity=LogLevel.SECURITY,
            user_id=user_id,
            ip_address=context.get('ip_address'),
            user_agent=context.get('user_agent'),
            device_fingerprint=context.get('device_id'),
            fraud_indicators=context.get('fraud_indicators', []),
            event_details={'reason': reason}
        ))

    def _write_log_entry(self, entry: AuditLogEntry) -> None:
        """Write log entry to file.

        Args:
            entry: Log entry to write
        """
        log_line = json.dumps({
            'timestamp': entry.timestamp.isoformat(),
            'event_type': entry.event_type,
            'severity': entry.severity,
            'user_id': entry.user_id,
            'session_id': entry.session_id,
            'ip_address': entry.ip_address,
            'user_agent': entry.user_agent,
            'device_id': entry.device_fingerprint,
            'risk_score': entry.risk_score,
            'risk_level': entry.risk_level,
            'fraud_indicators': entry.fraud_indicators,
            'event_details': entry.event_details,
            'outcome': entry.outcome,
            'actor': entry.actor,
        })

        with open(self.log_file, 'a') as f:
            f.write(log_line + '\n')

    def _get_severity_from_risk(self, risk_score: RiskScore, outcome: str) -> str:
        """Determine severity level from risk assessment and outcome."""
        if outcome == 'success' and risk_score.risk_level == RiskLevel.LOW:
            return LogLevel.INFO
        elif outcome == 'failure' and risk_score.risk_level in [RiskLevel.LOW, RiskLevel.MEDIUM]:
            return LogLevel.WARN
        elif outcome == 'failure' and risk_score.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            return LogLevel.AUDIT
        else:
            return LogLevel.SECURITY

    def export_logs(self,
                   start_time: datetime,
                   end_time: datetime,
                   user_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Export logs for analysis or reporting.

        Args:
            start_time: Start time for log export
            end_time: End time for log export
            user_id: Optional user ID to filter logs

        Returns:
            List of log entries
        """
        logs = []

        with open(self.log_file, 'r') as f:
            for line in f:
                try:
                    log_entry = json.loads(line.strip())
                    entry_time = datetime.fromisoformat(log_entry['timestamp'])

                    if entry_time < start_time or entry_time > end_time:
                        continue

                    if user_id and log_entry.get('user_id') != user_id:
                        continue

                    logs.append(log_entry)
                except Exception:
                    continue

        return logs

    def generate_security_report(self,
                                 start_time: datetime,
                                 end_time: datetime) -> dict[str, Any]:
        """Generate security report for given time period.

        Args:
            start_time: Start time for report
            end_time: End time for report

        Returns:
            Security report summary
        """
        logs = self.export_logs(start_time, end_time)

        report = {
            'period_start': start_time.isoformat(),
            'period_end': end_time.isoformat(),
            'total_events': len(logs),
            'event_types': {},
            'risk_levels': {},
            'success_rate': 0.0,
            'fraud_detection_rate': 0.0,
            'security_violations': 0,
        }

        for log_entry in logs:
            event_type = log_entry.get('event_type', '')
            risk_level = log_entry.get('risk_level', '')
            outcome = log_entry.get('outcome', '')

            report['event_types'][event_type] = report['event_types'].get(event_type, 0) + 1

            if risk_level:
                report['risk_levels'][risk_level] = report['risk_levels'].get(risk_level, 0) + 1

            if outcome == 'success':
                report['success_rate'] += 1

            if event_type in [str(AuditEventType.FRAUD_DETECTION)]:
                report['fraud_detection_rate'] += 1

            if event_type in [str(AuditEventType.SECURITY_VIOLATION)]:
                report['security_violations'] += 1

        if report['total_events'] > 0:
            report['success_rate'] = report['success_rate'] / report['total_events']
            report['fraud_detection_rate'] = report['fraud_detection_rate'] / report['total_events']

        return report

    def export_security_events(self,
                              event_types: Optional[list[AuditEventType]] = None) -> list[dict[str, Any]]:
        """Export specific security events for analysis.

        Args:
            event_types: Optional list of event types to filter

        Returns:
            List of security events
        """
        logs = self.export_logs(
            datetime.utcnow() - timedelta(days=30),
            datetime.utcnow()
        )

        if event_types:
            logs = [log for log in logs if log.get('event_type') in [et.value for et in event_types]]

        return logs

    def get_risk_scores(self, user_id: str) -> list[dict[str, Any]]:
        """Get risk scores for a specific user.

        Args:
            user_id: User identifier

        Returns:
            List of risk scores for the user
        """
        return self.export_logs(
            datetime.utcnow() - timedelta(days=30),
            datetime.utcnow(),
            user_id
        )


# Singleton instances
_risk_audit_logger: Optional[RiskAuditLogger] = None
_security_event_detector: Optional[SecurityEventDetector] = None


def get_risk_audit_logger(log_file: str = "risk_audit.log") -> RiskAuditLogger:
    """Get or create risk audit logger instance."""
    global _risk_audit_logger
    if _risk_audit_logger is None:
        _risk_audit_logger = RiskAuditLogger(log_file)
    return _risk_audit_logger


def get_security_event_detector(logger_name: str = 'security_audit') -> SecurityEventDetector:
    """Get or create security event detector instance."""
    global _security_event_detector
    if _security_event_detector is None:
        _security_event_detector = SecurityEventDetector(logging.getLogger(logger_name))
    return _security_event_detector


def log_auth_attempt(user_id: str,
                    context: dict[str, Any],
                    risk_score: RiskScore,
                    outcome: str,
                    details: Optional[dict[str, Any]] = None,
                    log_file: str = "risk_audit.log") -> None:
    """Convenience function to log authentication attempt.

    Args:
        user_id: User identifier
        context: Request context
        risk_score: Risk assessment result
        outcome: Authentication outcome
        details: Additional event details
        log_file: Path to audit log file
    """
    risk_audit_logger = get_risk_audit_logger(log_file)
    risk_audit_logger.log_authentication_attempt(user_id, context, risk_score, outcome, details)


def log_risk_assessment(user_id: str,
                       context: dict[str, Any],
                       risk_score: RiskScore,
                       log_file: str = "risk_audit.log") -> None:
    """Convenience function to log risk assessment.

    Args:
        user_id: User identifier
        context: Request context
        risk_score: Risk assessment result
        log_file: Path to audit log file
    """
    risk_audit_logger = get_risk_audit_logger(log_file)
    risk_audit_logger.log_risk_assessment(user_id, risk_score, context)


def log_access_denied(user_id: str,
                     context: dict[str, Any],
                     reason: str,
                     log_file: str = "risk_audit.log") -> None:
    """Convenience function to log access denial.

    Args:
        user_id: User identifier
        context: Request context
        reason: Reason for access denial
        log_file: Path to audit log file
    """
    risk_audit_logger = get_risk_audit_logger(log_file)
    risk_audit_logger.log_access_denied(user_id, context, reason)