"""Tests for mypackage."""
import pytest
from mypackage.utils import helper_function


def test_helper_function():
    """Test helper function."""
    assert helper_function(1, 2) == 3
