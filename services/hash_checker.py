"""
Hash checker service - checks if image hashes match known bad hashes.

Simple service that's easy to test without Discord dependencies.
"""
from __future__ import annotations

import hashlib
from typing import Optional


class HashChecker:
    """
    Checks image hashes against a set of known bad hashes.
    
    This is a pure Python class with no Discord dependencies,
    making it easy to unit test.
    """

    def __init__(self, hashes: Optional[set[str]] = None) -> None:
        self.hashes: set[str] = hashes or set()

    def add_hash(self, hash_value: str) -> None:
        """Add a hash to the set."""
        self.hashes.add(hash_value.lower())

    def add_hashes(self, hash_values: list[str]) -> None:
        """Add multiple hashes to the set."""
        for h in hash_values:
            self.add_hash(h)

    def remove_hash(self, hash_value: str) -> None:
        """Remove a hash from the set."""
        self.hashes.discard(hash_value.lower())

    def clear(self) -> None:
        """Clear all hashes."""
        self.hashes.clear()

    def check(self, hash_value: str) -> bool:
        """Check if a hash matches any known bad hash."""
        return hash_value.lower() in self.hashes

    def __contains__(self, hash_value: str) -> bool:
        """Allow `hash in checker` syntax."""
        return self.check(hash_value)

    def __len__(self) -> int:
        """Return number of hashes."""
        return len(self.hashes)

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        """Compute SHA256 hash of bytes."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def magic_bytes_valid(data: bytes) -> bool:
        """
        Check if data has valid image magic bytes.
        
        Supports: PNG, JPEG, GIF, BMP, TIFF, ICO, WEBP
        """
        if len(data) < 12:
            return False
        
        # Allow optional UTF-8 BOM before PNG signature
        if data.startswith(b"\xEF\xBB\xBF"):
            data = data[3:]
        
        haystack = data[:512]
        
        signatures = [
            b"\x89PNG\r\n\x1a\n",  # PNG
            b"\xFF\xD8\xFF",       # JPEG
            b"GIF87a",             # GIF87
            b"GIF89a",             # GIF89
            b"BM",                 # BMP
            b"II*\x00",            # TIFF (little-endian)
            b"MM\x00*",            # TIFF (big-endian)
            b"\x00\x00\x01\x00",   # ICO
        ]
        
        for sig in signatures:
            if sig in haystack:
                return True
        
        # Looser WEBP check
        if b"RIFF" in haystack and b"WEBP" in haystack:
            return True
        
        return False
