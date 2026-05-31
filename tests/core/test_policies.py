import pytest

from neurocore.core.policies import enforce_sensitivity_ceiling, validate_bucket


def test_validate_bucket_rejects_invalid_bucket_names():
    with pytest.raises(ValueError, match="bucket"):
        validate_bucket("invalid bucket")


def test_enforce_sensitivity_ceiling_blocks_more_sensitive_content():
    enforce_sensitivity_ceiling("standard", "restricted")
    enforce_sensitivity_ceiling("restricted", "restricted")

    with pytest.raises(PermissionError, match="sensitivity"):
        enforce_sensitivity_ceiling("sealed", "restricted")
