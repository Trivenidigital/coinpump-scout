"""Tests for Pump.fun graduated token detection."""
import pytest
from unittest.mock import AsyncMock, patch
from scout.ingestion.pumpfun import _is_pumpfun_token


def test_is_pumpfun_token_valid():
    """Solana address ending with 'pump' is a pump.fun token."""
    assert _is_pumpfun_token({"chainId": "solana", "tokenAddress": "abc123pump"}) is True


def test_is_pumpfun_token_wrong_chain():
    """Non-Solana chain is not a pump.fun token."""
    assert _is_pumpfun_token({"chainId": "ethereum", "tokenAddress": "abc123pump"}) is False


def test_is_pumpfun_token_no_pump_suffix():
    """Solana address without 'pump' suffix is not a pump.fun token."""
    assert _is_pumpfun_token({"chainId": "solana", "tokenAddress": "abc123"}) is False


def test_is_pumpfun_token_empty_address():
    """Empty address is not a pump.fun token."""
    assert _is_pumpfun_token({"chainId": "solana", "tokenAddress": ""}) is False


def test_is_pumpfun_token_missing_fields():
    """Missing fields default to empty strings, returning False."""
    assert _is_pumpfun_token({}) is False
