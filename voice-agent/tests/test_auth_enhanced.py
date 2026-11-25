from __future__ import annotations

from pathlib import Path

import pytest

from voice_agent.auth_enhanced.risk_scoring import (
    AudioQualityAnalyzer,
    AudioQualityMetrics,
    ContextRiskAnalyzer,
    DeviceRiskAnalyzer,
    RiskCache,
    RiskLevel,
    RiskScore,
    RiskScoringEngine,
    calculate_auth_risk,
)
from voice_agent.auth_enhanced.adaptive_challenge import (
    AdaptiveChallenge,
    SecurityAction,
)
from voice_agent.config import AppConfig


@pytest.fixture
def config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def risk_engine(config: AppConfig) -> RiskScoringEngine:
    return RiskScoringEngine(config)


@pytest.fixture
def challenge(config: AppConfig) -> AdaptiveChallenge:
    return AdaptiveChallenge(config)


class TestAudioQualityAnalyzer:
    def test_high_quality_audio(self):
        features = AudioQualityMetrics(
            snr_db=40.0,
            signal_strength=0.9,
            duration_seconds=5.0,
            energy_mean=0.5,
            energy_std=0.05,
            zero_crossings=0.3,
            spectral_centroid=1200.0,
        )
        risk = AudioQualityAnalyzer.calculate_audio_risk(features)
        assert risk < 0.3

    def test_low_quality_audio(self):
        features = AudioQualityMetrics(
            snr_db=3.0,
            signal_strength=0.05,
            duration_seconds=0.5,
            energy_mean=1.0,
            energy_std=0.4,
            zero_crossings=0.05,
            spectral_centroid=300.0,
        )
        risk = AudioQualityAnalyzer.calculate_audio_risk(features)
        assert risk > 0.7


class TestDeviceRiskAnalyzer:
    def test_legitimate_device(self):
        risk, factors = DeviceRiskAnalyzer.calculate_device_risk(
            "device_abc_123", "Mozilla/5.0 (compatible; Chrome/120)"
        )
        assert risk < 0.5
        assert "suspicious_device_id" not in factors

    def test_suspicious_device(self):
        risk, factors = DeviceRiskAnalyzer.calculate_device_risk(
            "automated_bot_script", "curl/7.68"
        )
        assert risk > 0.5
        assert "suspicious_device_id" in factors


class TestContextRiskAnalyzer:
    def test_normal_hours(self):
        time_ms = 10 * 3600 * 1000
        risk, factors = ContextRiskAnalyzer.calculate_context_risk(
            time_ms, "192.168.1.100", "scott"
        )
        assert risk < 0.5

    def test_unusual_hours(self):
        time_ms = 3 * 3600 * 1000
        risk, factors = ContextRiskAnalyzer.calculate_context_risk(
            time_ms, "192.168.1.100", "scott"
        )
        assert "unusual_login_time" in factors

    def test_suspicious_ip(self):
        time_ms = 12 * 3600 * 1000
        risk, factors = ContextRiskAnalyzer.calculate_context_risk(
            time_ms, "vpn.exit.node", "scott"
        )
        assert "suspicious_ip_pattern" in factors


class TestRiskScoringEngine:
    def test_low_risk_scenario(self, risk_engine: RiskScoringEngine):
        audio_data = {"snr_db": 35.0, "duration_seconds": 5.0, "energy_std": 0.05}
        context = {"timestamp": 10 * 3600000, "ip_address": "192.168.1.100", "device_id": "valid_device", "user_agent": "Mozilla/5.0 (compatible; Chrome/120)"}
        score = risk_engine.calculate_risk(audio_data, context)
        assert score.risk_level == RiskLevel.LOW
        assert score.confidence_score < 0.4

    def test_elevated_risk_scenario(self, risk_engine: RiskScoringEngine):
        audio_data = {"snr_db": 3.0, "duration_seconds": 0.5, "energy_std": 0.4}
        context = {"timestamp": 3 * 3600000, "ip_address": "tor.exit.node.proxy", "device_id": "automated_bot", "user_agent": "curl/7.68"}
        score = risk_engine.calculate_risk(audio_data, context)
        assert score.risk_level in [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        assert score.confidence_score > 0.5
        assert len(score.fraud_indicators) > 0

    def test_risk_score_structure(self, risk_engine: RiskScoringEngine):
        audio_data = {"snr_db": 20.0, "duration_seconds": 3.0}
        context = {"timestamp": 14 * 3600000, "ip_address": "10.0.0.1", "device_id": "device_1", "user_agent": "Mozilla/5.0"}
        score = risk_engine.calculate_risk(audio_data, context)
        assert hasattr(score, "risk_level")
        assert hasattr(score, "confidence_score")
        assert hasattr(score, "audio_risk")
        assert hasattr(score, "context_risk")
        assert hasattr(score, "device_risk")
        assert hasattr(score, "temporal_risk")
        assert hasattr(score, "fraud_indicators")
        assert hasattr(score, "recommendations")
        assert hasattr(score, "metadata")


class TestAdaptiveChallenge:
    def test_low_risk_action(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.LOW,
            confidence_score=0.8,
            audio_risk=0.1,
            context_risk=0.1,
            device_risk=0.1,
            temporal_risk=0.1,
            fraud_indicators=[],
            recommendations=["Standard authentication"],
            metadata={},
        )
        action = challenge.get_security_action(score)
        assert action == SecurityAction.STANDARD

    def test_critical_risk_action(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.CRITICAL,
            confidence_score=0.95,
            audio_risk=0.9,
            context_risk=0.9,
            device_risk=0.8,
            temporal_risk=0.9,
            fraud_indicators=["suspicious_device_id", "suspicious_ip_pattern"],
            recommendations=["Reject authentication"],
            metadata={},
        )
        action = challenge.get_security_action(score)
        assert action == SecurityAction.REJECT

    def test_high_risk_mfa(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.HIGH,
            confidence_score=0.8,
            audio_risk=0.6,
            context_risk=0.7,
            device_risk=0.5,
            temporal_risk=0.6,
            fraud_indicators=[],
            recommendations=["Require MFA"],
            metadata={},
        )
        action = challenge.get_security_action(score)
        assert action == SecurityAction.MFA_REQUIRED

    def test_low_confidence_enhanced(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.LOW,
            confidence_score=0.1,
            audio_risk=0.05,
            context_risk=0.2,
            device_risk=0.1,
            temporal_risk=0.05,
            fraud_indicators=[],
            recommendations=["Standard authentication"],
            metadata={},
        )
        action = challenge.get_security_action(score)
        assert action == SecurityAction.ENHANCED

    def test_challenge_requirements_low(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.LOW,
            confidence_score=0.1,
            audio_risk=0.05,
            context_risk=0.1,
            device_risk=0.1,
            temporal_risk=0.05,
            fraud_indicators=[],
            recommendations=["Standard authentication"],
            metadata={},
        )
        reqs = challenge.get_challenge_requirements(score)
        assert len(reqs["requirements"]) == 1
        assert reqs["requirements"][0]["type"] == "voice"

    def test_challenge_requirements_high(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.HIGH,
            confidence_score=0.8,
            audio_risk=0.6,
            context_risk=0.7,
            device_risk=0.5,
            temporal_risk=0.6,
            fraud_indicators=[],
            recommendations=["Require MFA"],
            metadata={},
        )
        reqs = challenge.get_challenge_requirements(score)
        assert any(r["type"] == "mfa_code" for r in reqs["requirements"])

    def test_should_enforce_critical(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.CRITICAL,
            confidence_score=0.95,
            audio_risk=0.9,
            context_risk=0.8,
            device_risk=0.7,
            temporal_risk=0.8,
            fraud_indicators=[],
            recommendations=["Reject"],
            metadata={},
        )
        enforce, reason = challenge.should_enforce_challenge(score)
        assert enforce
        assert "MFA required" in reason

    def test_recommendation_for_user(self, challenge: AdaptiveChallenge):
        score = RiskScore(
            risk_level=RiskLevel.LOW,
            confidence_score=0.1,
            audio_risk=0.05,
            context_risk=0.1,
            device_risk=0.1,
            temporal_risk=0.05,
            fraud_indicators=[],
            recommendations=["Standard authentication"],
            metadata={},
        )
        msg = challenge.get_recommendation_for_user(score)
        assert "accepted" in msg


class TestRiskCache:
    def test_cache_hit(self):
        cache = RiskCache(max_size=10)
        score = RiskScore(
            risk_level=RiskLevel.LOW,
            confidence_score=0.1,
            audio_risk=0.05,
            context_risk=0.1,
            device_risk=0.1,
            temporal_risk=0.05,
            fraud_indicators=[],
            recommendations=["Standard"],
            metadata={},
        )
        cache.set("key1", score)
        cached = cache.get("key1")
        assert cached is score

    def test_cache_miss(self):
        cache = RiskCache(max_size=10)
        assert cache.get("nonexistent") is None

    def test_cache_eviction(self):
        cache = RiskCache(max_size=2)
        score1 = RiskScore(RiskLevel.LOW, 0.1, 0.1, 0.1, 0.1, 0.1, [], [], {})
        score2 = RiskScore(RiskLevel.LOW, 0.2, 0.1, 0.1, 0.1, 0.1, [], [], {})
        score3 = RiskScore(RiskLevel.LOW, 0.3, 0.1, 0.1, 0.1, 0.1, [], [], {})
        cache.set("k1", score1)
        cache.set("k2", score2)
        cache.set("k3", score3)
        assert cache.get("k1") is None
        assert cache.get("k3") is score3

    def test_cache_clear(self):
        cache = RiskCache(max_size=10)
        score = RiskScore(RiskLevel.LOW, 0.1, 0.1, 0.1, 0.1, 0.1, [], [], {})
        cache.set("k1", score)
        cache.clear()
        assert cache.get("k1") is None


class TestCalculateAuthRisk:
    def test_convenience_function(self, config: AppConfig):
        audio_data = {"snr_db": 30.0, "duration_seconds": 4.0}
        context = {"timestamp": 14 * 3600000, "ip_address": "10.0.0.1", "device_id": "d1", "user_agent": "Mozilla/5.0"}
        score = calculate_auth_risk(audio_data, context, config)
        assert isinstance(score, RiskScore)
        assert score.risk_level == RiskLevel.LOW
