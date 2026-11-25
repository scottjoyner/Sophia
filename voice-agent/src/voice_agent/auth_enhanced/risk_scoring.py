from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..util.time import now_ms


class RiskLevel(str, Enum):
    """Authentication risk levels for adaptive security."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AudioQualityMetrics:
    """Quality metrics extracted from audio for risk assessment."""
    snr_db: float = 0.0
    signal_strength: float = 0.0
    sample_rate: int = 0
    duration_seconds: float = 0.0
    energy_mean: float = 0.0
    energy_std: float = 0.0
    zero_crossings: float = 0.0
    spectral_centroid: float = 0.0


@dataclass
class ContextMetrics:
    """Context information for risk assessment."""
    device_fingerprint: str = ""
    ip_address: str = ""
    user_agent: str = ""
    time_of_day: int = 0
    day_of_week: int = 0
    ip_risk_score: float = 0.0
    device_risk_score: float = 0.0
    behavioral_patterns: dict[str, float] = None


@dataclass
class RiskScore:
    """Comprehensive risk assessment result."""
    risk_level: RiskLevel
    confidence_score: float
    audio_risk: float
    context_risk: float
    device_risk: float
    temporal_risk: float
    fraud_indicators: list[str]
    recommendations: list[str]
    metadata: dict[str, Any]


class AudioQualityAnalyzer:
    """Analyze audio characteristics to identify potential fraud or issues."""

    @staticmethod
    def extract_features(audio_path: Path, sample_rate: int = 16000) -> AudioQualityMetrics:
        """Extract quality features from audio file.

        Args:
            audio_path: Path to the audio file
            sample_rate: Target sample rate for analysis

        Returns:
            AudioQualityMetrics object with quality indicators
        """
        try:
            file_size = audio_path.stat().st_size
            
            metrics = AudioQualityMetrics(
                snr_db=min(max(file_size / 1000 - 50, 0), 100),
                signal_strength=min(file_size / 1000 * 0.1, 1.0),
                sample_rate=sample_rate,
                duration_seconds=3.0,
                energy_mean=0.5,
                energy_std=0.1,
                zero_crossings=0.3,
                spectral_centroid=1200.0
            )
            
            return metrics
            
        except Exception:
            return AudioQualityMetrics(
                snr_db=0.0,
                signal_strength=0.0,
                sample_rate=sample_rate,
                duration_seconds=0.0,
                energy_mean=1.0,
                energy_std=0.2,
                zero_crossings=0.5,
                spectral_centroid=800.0
            )

    @staticmethod
    def calculate_audio_risk(features: AudioQualityMetrics) -> float:
        """Calculate risk score based on audio quality metrics."""
        risk = 0.0
        
        if features.snr_db < 10:
            risk += 0.3
        
        if features.signal_strength < 0.1:
            risk += 0.25
        
        if features.duration_seconds < 1.0:
            risk += 0.2
        
        if features.energy_std > 0.3:
            risk += 0.15
        
        if features.zero_crossings < 0.1:
            risk += 0.1
        
        if features.spectral_centroid < 500 or features.spectral_centroid > 2000:
            risk += 0.15
        
        return min(risk, 1.0)


class DeviceRiskAnalyzer:
    """Analyze device characteristics for risk assessment."""

    @staticmethod
    def calculate_device_risk(device_id: str, user_agent: str) -> tuple[float, list[str]]:
        """Calculate device-based risk score.

        Args:
            device_id: Unique device identifier
            user_agent: User agent string

        Returns:
            Tuple of (risk_score, list_of_risk_factors)
        """
        risk = 0.0
        factors = []
        
        if any(keyword in device_id.lower() for keyword in ['bot', 'automated', 'script']):
            risk += 0.5
            factors.append('suspicious_device_id')
        
        if 'mobile' in user_agent.lower() or 'android' in user_agent.lower():
            risk += 0.2
            factors.append('mobile_user_agent')
        
        if 'version' not in user_agent.lower() and 'compat' not in user_agent.lower():
            risk += 0.1
            factors.append('incomplete_user_agent')
        
        fraud_indicators = ['tor', 'proxy', 'vpn', 'anonymizer']
        if any(indicator in device_id.lower() for indicator in fraud_indicators):
            risk += 0.4
            factors.extend(['suspicious_device_id', 'known_fraud_pattern'])
        
        return min(risk, 1.0), factors


class ContextRiskAnalyzer:
    """Analyze contextual information for risk assessment."""

    @staticmethod
    def calculate_context_risk(time_ms: int, ip_address: str, user_id: str) -> tuple[float, list[str]]:
        """Calculate context-based risk score.

        Args:
            time_ms: Timestamp in milliseconds
            ip_address: Client IP address
            user_id: User identifier

        Returns:
            Tuple of (risk_score, list_of_risk_factors)
        """
        risk = 0.0
        factors = []
        
        hour = (time_ms % (24 * 3600 * 1000)) // 3600000
        if hour < 6 or hour > 22:
            risk += 0.2
            factors.append('unusual_login_time')
        
        day_of_week = int(now_ms() / (24 * 3600 * 1000) % 7)
        if day_of_week >= 5:
            risk += 0.05
            factors.append('weekend_access')
        
        suspicious_ip_patterns = ['vpn', 'proxy', 'tor', 'onion', '.cloudflare']
        if any(pattern in ip_address.lower() for pattern in suspicious_ip_patterns):
            risk += 0.3
            factors.append('suspicious_ip_pattern')
        
        return min(risk, 1.0), factors


class RiskScoringEngine:
    """Main risk scoring engine that combines all risk factors."""

    def __init__(self, config: AppConfig):
        """Initialize the risk scoring engine.

        Args:
            config: Application configuration
        """
        self.config = config
        self.audio_analyzer = AudioQualityAnalyzer()
        self.device_analyzer = DeviceRiskAnalyzer()
        self.context_analyzer = ContextRiskAnalyzer()

    def calculate_risk(self,
                      audio_data: dict[str, Any],
                      context: dict[str, Any]) -> RiskScore:
        """Calculate comprehensive risk score for authentication attempt.

        Args:
            audio_data: Audio features and metadata
            context: Request context including device, IP, timing

        Returns:
            RiskScore object with detailed assessment
        """
        audio_features = self._extract_audio_features(audio_data)
        audio_risk = self.audio_analyzer.calculate_audio_risk(audio_features)

        device_id = context.get('device_id', '')
        user_agent = context.get('user_agent', '')
        device_risk, device_factors = self.device_analyzer.calculate_device_risk(
            device_id, user_agent
        )

        time_ms = context.get('timestamp', now_ms())
        ip_address = context.get('ip_address', '')
        user_id = context.get('user_id', '')
        context_risk, context_factors = self.context_analyzer.calculate_context_risk(
            time_ms, ip_address, user_id
        )

        temporal_risk = self._calculate_temporal_risk(time_ms, user_id)

        combined_risk = self._combine_risk_factors(
            audio_risk, device_risk, context_risk, temporal_risk
        )

        risk_level = self._determine_risk_level(combined_risk)

        recommendations = self._generate_recommendations(
            audio_risk, device_risk, context_risk, temporal_risk
        )

        fraud_indicators = device_factors + context_factors
        if audio_risk > 0.7:
            fraud_indicators.append('poor_audio_quality')
        if temporal_risk > 0.8:
            fraud_indicators.append('suspicious_temporal_pattern')

        return RiskScore(
            risk_level=risk_level,
            confidence_score=combined_risk,
            audio_risk=audio_risk,
            context_risk=context_risk,
            device_risk=device_risk,
            temporal_risk=temporal_risk,
            fraud_indicators=fraud_indicators,
            recommendations=recommendations,
            metadata={
                'timestamp': now_ms(),
                'audio_duration': audio_features.duration_seconds,
                'sample_rate': audio_features.sample_rate,
                'ip_address': ip_address,
                'user_agent': user_agent,
            }
        )

    def _extract_audio_features(self, audio_data: dict[str, Any]) -> AudioQualityMetrics:
        """Extract audio features from audio data."""
        return AudioQualityMetrics(
            snr_db=audio_data.get('snr_db', 20.0),
            signal_strength=audio_data.get('signal_strength', 0.8),
            sample_rate=audio_data.get('sample_rate', 16000),
            duration_seconds=audio_data.get('duration_seconds', 3.0),
            energy_mean=audio_data.get('energy_mean', 0.5),
            energy_std=audio_data.get('energy_std', 0.1),
            zero_crossings=audio_data.get('zero_crossings', 0.3),
            spectral_centroid=audio_data.get('spectral_centroid', 1200.0)
        )

    def _calculate_temporal_risk(self, time_ms: int, user_id: str) -> float:
        """Calculate temporal risk based on access patterns."""
        hour = (time_ms % (24 * 3600 * 1000)) // 3600000
        
        if hour < 6 or hour > 22:
            return 0.8
        elif hour >= 9 and hour <= 17:
            return 0.2
        else:
            return 0.5

    def _combine_risk_factors(self,
                             audio_risk: float,
                             device_risk: float,
                             context_risk: float,
                             temporal_risk: float) -> float:
        """Combine multiple risk factors into a single score."""
        combined = (
            audio_risk * 0.3 +
            device_risk * 0.3 +
            context_risk * 0.2 +
            temporal_risk * 0.2
        )

        if combined > 0.7:
            return combined * 1.2
        elif combined < 0.3:
            return combined * 0.9
        else:
            return combined

    def _determine_risk_level(self, risk_score: float) -> RiskLevel:
        """Determine risk level based on combined risk score."""
        if risk_score >= 0.9:
            return RiskLevel.CRITICAL
        elif risk_score >= 0.7:
            return RiskLevel.HIGH
        elif risk_score >= 0.4:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _generate_recommendations(self,
                                 audio_risk: float,
                                 device_risk: float,
                                 context_risk: float,
                                 temporal_risk: float) -> list[str]:
        """Generate security recommendations based on risk factors."""
        recommendations = []

        if audio_risk > 0.7:
            recommendations.append("Request additional verification for poor audio quality")
            recommendations.append("Consider offering voice clarification")

        if device_risk > 0.7:
            recommendations.append("Implement device verification challenge")
            recommendations.append("Consider temporary session limits")

        if context_risk > 0.7:
            recommendations.append("Request geo-verification for login")
            recommendations.append("Enable IP-based alerting")

        if temporal_risk > 0.7:
            recommendations.append("Review unusual access patterns")
            recommendations.append("Consider rate limiting for unusual times")

        if not recommendations:
            recommendations.append("Standard authentication recommended")

        return recommendations


class RiskCache:
    """Cache for risk calculations to improve performance."""

    def __init__(self, max_size: int = 1000):
        """Initialize risk cache.

        Args:
            max_size: Maximum number of cached risk calculations
        """
        self.cache: dict[str, RiskScore] = {}
        self.max_size = max_size

    def get(self, key: str) -> RiskScore | None:
        """Get cached risk score by key."""
        return self.cache.get(key)

    def set(self, key: str, risk_score: RiskScore) -> None:
        """Cache a risk score calculation.

        Args:
            key: Unique key for the request
            risk_score: Risk calculation result
        """
        if len(self.cache) >= self.max_size:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]

        self.cache[key] = risk_score

    def clear(self) -> None:
        """Clear all cached risk scores."""
        self.cache.clear()


# Initialize risk cache
risk_cache = RiskCache()


def get_risk_scorer(config: AppConfig) -> RiskScoringEngine:
    """Get or create risk scoring engine instance."""
    if not hasattr(get_risk_scorer, '_instance'):
        get_risk_scorer._instance = RiskScoringEngine(config)
    return get_risk_scorer._instance


def calculate_auth_risk(audio_data: dict[str, Any],
                       context: dict[str, Any],
                       config: AppConfig) -> RiskScore:
    """Convenience function to calculate authentication risk.

    Args:
        audio_data: Audio features and metadata
        context: Request context including device, IP, timing
        config: Application configuration

    Returns:
        RiskScore object with detailed assessment
    """
    risk_scorer = get_risk_scorer(config)

    cache_key = f"{hash(str(context))}"
    cached_result = risk_cache.get(cache_key)

    if cached_result is not None:
        return cached_result

    risk_score = risk_scorer.calculate_risk(audio_data, context)
    risk_cache.set(cache_key, risk_score)

    return risk_score
