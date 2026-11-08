"""
EverAI Device Profile Spoofing and Client Fingerprint Randomizer.
Provides realistic Desktop Chromium / Edge device signatures, Client Hints,
and language variations to prevent bot correlation across requests.
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class DeviceProfile:
    """Represents a simulated browser device profile with matching Client Hints and TLS fingerprint."""
    name: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    accept_language: str
    impersonate: str = "chrome131"

    def to_headers(self) -> Dict[str, str]:
        """Returns standard HTTP headers matching this device profile."""
        return {
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "Accept-Language": self.accept_language,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }


# Catalog of modern, realistic Desktop Browser Profiles with matching TLS Impersonate Targets
DEVICE_PRESETS: List[Dict[str, str]] = [
    {
        "name": "Google Chrome 132 on Windows 11",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "name": "Google Chrome 131 on Windows 11",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "name": "Google Chrome 130 on Windows 10",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome124",
    },
    {
        "name": "Google Chrome 129 on Windows 10",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome124",
    },
    {
        "name": "Microsoft Edge 131 on Windows 11",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "sec_ch_ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "name": "Microsoft Edge 130 on Windows 10",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        "sec_ch_ua": '"Microsoft Edge";v="130", "Chromium";v="130", "Not?A_Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
        "impersonate": "chrome124",
    },
    {
        "name": "Google Chrome 132 on macOS Sequoia",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
        "impersonate": "chrome131",
    },
    {
        "name": "Google Chrome 131 on macOS Sonoma",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
        "impersonate": "chrome131",
    },
    {
        "name": "Google Chrome 130 on macOS",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
        "impersonate": "chrome124",
    },
]

LANGUAGE_PRESETS: List[str] = [
    "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "vi,en-US;q=0.9,en;q=0.8",
    "vi-VN,vi;q=0.9,en;q=0.8",
    "vi-VN,en-US;q=0.9,en;q=0.8,vi;q=0.7",
    "vi,en;q=0.9",
]


def get_random_device_profile(seed: Optional[int] = None) -> DeviceProfile:
    """
    Returns a newly generated random DeviceProfile with coherent UA, Client Hints, and TLS impersonation.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    preset = rng.choice(DEVICE_PRESETS)
    lang = rng.choice(LANGUAGE_PRESETS)

    return DeviceProfile(
        name=preset["name"],
        user_agent=preset["user_agent"],
        sec_ch_ua=preset["sec_ch_ua"],
        sec_ch_ua_mobile=preset["sec_ch_ua_mobile"],
        sec_ch_ua_platform=preset["sec_ch_ua_platform"],
        accept_language=lang,
        impersonate=preset.get("impersonate", "chrome131"),
    )
